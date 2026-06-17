import os
from flask import Flask
from threading import Thread

# خادم ويب صغير لإبقاء الخدمة حية على Render
app = Flask(__name__)


@app.route('/')
def home():
    return "بوت الأدوات يعمل ✅"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


Thread(target=run_web).start()

# ---------------- بوت تليجرام ----------------

import telebot
from telebot.types import BotCommand
from PIL import Image
import time
import glob
import threading

# التوكن يُقرأ من متغير البيئة في Render
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكنك_هنا")
bot = telebot.TeleBot(BOT_TOKEN)

# الحد الأقصى لعدد الصور في ملف PDF واحد
MAX_IMAGES = 50
# مهلة الانتظار بعد آخر صورة قبل التحويل التلقائي (ثوانٍ)
AUTO_DELAY = 3

bot.set_my_commands([
    BotCommand("start", "بدء الاستخدام"),
    BotCommand("done", "تحويل الصور إلى PDF"),
    BotCommand("cancel", "إلغاء وحذف الصور")
])

# تخزين مؤقت للصور لكل مستخدم
sessions = {}


class AutoTimer(threading.Thread):
    """مؤقت يحوّل الصور تلقائياً إذا لم تصل صورة جديدة خلال المهلة."""
    def __init__(self, chat_id):
        super().__init__()
        self.chat_id = chat_id
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        if self._cancel.wait(AUTO_DELAY):
            return
        sess = sessions.get(self.chat_id)
        if sess and sess.get("timer") is self and sess.get("files"):
            convert_and_send(self.chat_id)


WELCOME = (
    "📄 أهلاً بك في بوت تحويل الصور إلى PDF\n\n"
    "• أرسل صورة أو عدة صور، وسأحوّلها إلى ملف PDF.\n"
    "• إذا أرسلت عدة صور، سأجمعها كلها في ملف واحد بالترتيب.\n"
    "• بعد إرسال الصور اكتب /done لإنشاء الملف فوراً، "
    f"أو انتظر {AUTO_DELAY} ثوانٍ وسيُنشأ تلقائياً.\n"
    "• لإلغاء العملية اكتب /cancel.\n\n"
    "أرسل أول صورة الآن 📷"
)


@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.reply_to(message, WELCOME)


@bot.message_handler(commands=['cancel'])
def handle_cancel(message):
    chat_id = message.chat.id
    cleanup_session(chat_id)
    bot.reply_to(message, "تم الإلغاء وحذف الصور ✅\nأرسل صوراً جديدة في أي وقت 📷")


def cleanup_session(chat_id):
    sess = sessions.pop(chat_id, None)
    if sess:
        for f in sess.get("files", []):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = message.chat.id

    sess = sessions.get(chat_id)
    if sess is None:
        sess = {"files": [], "last": 0, "timer": None}
        sessions[chat_id] = sess

    if len(sess["files"]) >= MAX_IMAGES:
        bot.reply_to(message, f"⚠️ وصلت للحد الأقصى ({MAX_IMAGES} صورة). اكتب /done لإنشاء الملف.")
        return

    try:
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)

        idx = len(sess["files"])
        path = f"img_{chat_id}_{idx}_{int(time.time()*1000)}.jpg"
        with open(path, "wb") as f:
            f.write(downloaded)

        sess["files"].append(path)
        sess["last"] = time.time()

        if sess.get("timer"):
            sess["timer"].cancel()
        t = AutoTimer(chat_id)
        sess["timer"] = t
        t.start()

        bot.reply_to(message, f"📷 تم استلام الصورة ({len(sess['files'])}). "
                              f"أرسل المزيد أو اكتب /done.")
    except Exception as e:
        print(f"Photo error for {chat_id}: {e}")
        bot.reply_to(message, "حدث خطأ أثناء استلام الصورة، حاول مرة أخرى.")


@bot.message_handler(commands=['done'])
def handle_done(message):
    chat_id = message.chat.id
    sess = sessions.get(chat_id)
    if not sess or not sess.get("files"):
        bot.reply_to(message, "ما أرسلت أي صورة بعد 📷\nأرسل صورة وسأحوّلها إلى PDF.")
        return
    if sess.get("timer"):
        sess["timer"].cancel()
    convert_and_send(chat_id)


def convert_and_send(chat_id):
    sess = sessions.get(chat_id)
    if not sess or not sess.get("files"):
        return

    files = sess["files"]
    pdf_path = f"output_{chat_id}_{int(time.time())}.pdf"
    status = None
    try:
        status = bot.send_message(chat_id, "⏳ جاري إنشاء ملف PDF...")

        images = []
        for fp in files:
            if os.path.exists(fp):
                img = Image.open(fp)
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                images.append(img)

        if not images:
            bot.edit_message_text("ما فيه صور صالحة للتحويل ❌", chat_id, status.message_id)
            return

        images[0].save(pdf_path, "PDF", save_all=True, append_images=images[1:])

        with open(pdf_path, "rb") as f:
            bot.send_document(
                chat_id, f,
                visible_file_name="converted.pdf",
                caption=f"✅ تم تحويل {len(images)} صورة إلى PDF"
            )

        try:
            bot.delete_message(chat_id, status.message_id)
        except:
            pass

    except Exception as e:
        print(f"Convert error for {chat_id}: {e}")
        try:
            if status:
                bot.edit_message_text("حدث خطأ أثناء التحويل ❌", chat_id, status.message_id)
            else:
                bot.send_message(chat_id, "حدث خطأ أثناء التحويل ❌")
        except:
            pass
    finally:
        cleanup_session(chat_id)
        for p in glob.glob(f"output_{chat_id}_*.pdf"):
            try:
                os.remove(p)
            except:
                pass


@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_other(message):
    bot.reply_to(message, "📷 أرسل لي صورة وسأحوّلها إلى PDF.\nاكتب /start للمساعدة.")


print("✅ بوت الأدوات يعمل الآن...")
bot.infinity_polling()
