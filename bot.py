import os
import json
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
from telebot.types import (BotCommand, InlineKeyboardMarkup, InlineKeyboardButton)
from PIL import Image
from pypdf import PdfReader, PdfWriter
import io
import time
import glob
import zipfile
import threading
import requests
import base64

# ===== الإعدادات (تُقرأ من متغيرات البيئة في Render) =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكنك_هنا")
bot = telebot.TeleBot(BOT_TOKEN)

# ===== مفاتيح خدمات الذكاء الاصطناعي =====
GEMINI_KEY    = os.environ.get("GEMINI_API_KEY", "")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN  = os.environ.get("CF_API_TOKEN", "")

# نموذج Gemini للنصوص وتحليل الصور
GEMINI_MODEL = "gemini-2.5-flash"
# نموذج Cloudflare لتوليد الصور (سريع ومجاني)
CF_IMAGE_MODEL = "@cf/bytedance/stable-diffusion-xl-lightning"

ADMIN_ID = 1983356771
CHANNEL_USERNAME = "@filmaxpro"
YOUTUBE_LINK = "https://youtube.com/@mosleh_2003?si=SvJFyYLE85GRRjnZ"

# حد حجم الملف على تليجرام (50MB)
MAX_FILE_SIZE_MB = 50
# مهلة التجميع التلقائي بعد آخر ملف (ثوانٍ)
AUTO_DELAY = 3
# الحد الأقصى لعدد العناصر في عملية واحدة
MAX_ITEMS = 50

# ===== تتبّع المستخدمين (للأدمن) =====
USERS_FILE = "users.json"


def load_users():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_users(users):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(users), f)
    except Exception as e:
        print(f"save_users err: {e}")


known_users = load_users()
users_lock = threading.Lock()


def track_user(user_id):
    """يسجّل المستخدم إن كان جديداً."""
    with users_lock:
        if user_id not in known_users:
            known_users.add(user_id)
            save_users(known_users)


bot.set_my_commands([
    BotCommand("start", "بدء الاستخدام والقائمة"),
    BotCommand("ai", "💬 اسأل الذكاء الاصطناعي"),
    BotCommand("image", "🎨 توليد صورة بالنص"),
    BotCommand("translate", "🌐 ترجمة نص"),
    BotCommand("summarize", "📝 تلخيص نص"),
    BotCommand("done", "تنفيذ العملية الآن"),
    BotCommand("cancel", "إلغاء العملية الحالية")
])

# قائمة أوامر خاصة بالأدمن وحده (تظهر في اختصاراته فقط)
try:
    from telebot.types import BotCommandScopeChat
    bot.set_my_commands([
        BotCommand("start", "بدء الاستخدام والقائمة"),
        BotCommand("ai", "💬 اسأل الذكاء الاصطناعي"),
        BotCommand("image", "🎨 توليد صورة بالنص"),
        BotCommand("translate", "🌐 ترجمة نص"),
        BotCommand("summarize", "📝 تلخيص نص"),
        BotCommand("done", "تنفيذ العملية الآن"),
        BotCommand("cancel", "إلغاء العملية الحالية"),
        BotCommand("users", "👤 عدد مستخدمي البوت")
    ], scope=BotCommandScopeChat(ADMIN_ID))
except Exception as e:
    print(f"admin commands err: {e}")

# جلسة كل مستخدم: chat_id -> {"tool": اسم الأداة, "files": [...], "timer": مؤقت, "opt": خيار}
sessions = {}


# ====================== الاشتراك الإجباري ======================
def check_sub(user_id):
    """التحقق من اشتراك المستخدم في قناة التلجرام. عند أي خطأ نسمح (لا نعلّق البوت)."""
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return True


def subscription_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("اشترك في قناة التلجرام 📢", url=f"https://t.me/{CHANNEL_USERNAME[1:]}"),
        InlineKeyboardButton("اشترك في قناة اليوتيوب 📺", url=YOUTUBE_LINK),
        InlineKeyboardButton("تحقّقت ✅", callback_data="check_sub")
    )
    return markup


def require_sub(message):
    """يرجّع True إذا المستخدم مشترك أو الأدمن، وإلا يرسل رسالة الاشتراك ويرجّع False."""
    uid = message.chat.id
    track_user(uid)  # نسجّل كل من يتفاعل مع البوت
    if uid == ADMIN_ID:
        return True
    if check_sub(uid):
        return True
    bot.reply_to(message, "عذراً، يجب الاشتراك في قنواتنا أولاً للاستخدام 👇",
                 reply_markup=subscription_markup())
    return False


# ====================== القائمة الرئيسية ======================
TOOLS = {
    "img2pdf": "🖼️ صور ← PDF",
    "mergepdf": "📚 دمج عدة PDF",
    "pdf2img": "📄 PDF ← صور",
    "splitpdf": "✂️ استخراج صفحات PDF",
    "compressimg": "🗜️ ضغط الصور",
    "convertimg": "🔄 تحويل صيغة الصور",
    "zip": "🗂️ ضغط ملفات (ZIP)",
}


def main_menu(chat_id=None):
    markup = InlineKeyboardMarkup(row_width=1)
    for key, label in TOOLS.items():
        markup.add(InlineKeyboardButton(label, callback_data=f"tool|{key}"))
    return markup


WELCOME = (
    "🧰 أهلاً بك في بوت الأدوات\n\n"
    "اختر أداة من الأزرار بالأسفل لمعالجة الملفات، أو استخدم مميزات الذكاء الاصطناعي:\n\n"
    "💬 /ai — اسأل الذكاء الاصطناعي\n"
    "🎨 /image — توليد صورة بالنص\n"
    "🌐 /translate — ترجمة نص\n"
    "📝 /summarize — تلخيص نص\n"
    "🔍 أرسل صورة مباشرة (بدون أداة) لتحليلها\n\n"
    "• بعد إرسال ملفاتك اكتب /done للتنفيذ فوراً، "
    f"أو انتظر {AUTO_DELAY} ثوانٍ.\n"
    "• لإلغاء العملية في أي وقت اكتب /cancel."
)

# نصائح كل أداة
TOOL_HINTS = {
    "img2pdf": "🖼️ أرسل صورة أو عدة صور، وسأجمعها في ملف PDF واحد.",
    "mergepdf": "📚 أرسل ملفين PDF أو أكثر، وسأدمجها في ملف واحد بالترتيب.",
    "pdf2img": "📄 أرسل ملف PDF، وسأحوّل كل صفحة إلى صورة.",
    "splitpdf": "✂️ أرسل ملف PDF، ثم سأطلب منك أرقام الصفحات المطلوبة.",
    "compressimg": "🗜️ أرسل صورة أو عدة صور، وسأضغطها لتصغير حجمها.",
    "convertimg": "🔄 أرسل صورة، وسأعرض لك الصيغ المتاحة للتحويل.",
    "zip": "🗂️ أرسل عدة ملفات (صور/مستندات)، وسأضغطها في ملف ZIP واحد.",
}


def get_session(chat_id):
    return sessions.get(chat_id)


def reset_session(chat_id, tool=None):
    cleanup_files(chat_id)
    if tool:
        sessions[chat_id] = {"tool": tool, "files": [], "timer": None, "opt": None}
    else:
        sessions.pop(chat_id, None)


def cleanup_files(chat_id):
    sess = sessions.get(chat_id)
    if sess:
        for f in sess.get("files", []):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass


# مؤقت التنفيذ التلقائي
class AutoTimer(threading.Thread):
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
            # الأدوات التي تحتاج خطوة إضافية لا تُنفَّذ تلقائياً
            if sess["tool"] not in ("splitpdf", "convertimg"):
                process(self.chat_id)


def arm_timer(chat_id):
    sess = sessions.get(chat_id)
    if not sess:
        return
    if sess.get("timer"):
        sess["timer"].cancel()
    t = AutoTimer(chat_id)
    sess["timer"] = t
    t.start()


# ====================== دوال الذكاء الاصطناعي ======================
def gemini_text(prompt, system=None):
    """يرسل نصاً إلى Gemini ويعيد الرد النصي."""
    if not GEMINI_KEY:
        return "⚠️ خدمة الذكاء الاصطناعي غير مهيأة (مفتاح Gemini ناقص)."
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}")
    parts = [{"text": prompt}]
    payload = {"contents": [{"parts": parts}]}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    try:
        r = requests.post(url, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"gemini_text err: {e}")
        return "❌ تعذّر الحصول على رد من الذكاء الاصطناعي. حاول مرة أخرى."


def gemini_vision(image_bytes, prompt, mime="image/jpeg"):
    """يحلّل صورة عبر Gemini ويعيد وصفاً نصياً."""
    if not GEMINI_KEY:
        return "⚠️ خدمة الذكاء الاصطناعي غير مهيأة (مفتاح Gemini ناقص)."
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}")
    b64 = base64.b64encode(image_bytes).decode()
    payload = {"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": mime, "data": b64}}
    ]}]}
    try:
        r = requests.post(url, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"gemini_vision err: {e}")
        return "❌ تعذّر تحليل الصورة. حاول مرة أخرى."


def cf_generate_image(prompt):
    """يولّد صورة عبر Cloudflare Workers AI. يعيد bytes الصورة أو None."""
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        return None, "⚠️ خدمة توليد الصور غير مهيأة (مفاتيح Cloudflare ناقصة)."
    url = (f"https://api.cloudflare.com/client/v4/accounts/"
           f"{CF_ACCOUNT_ID}/ai/run/{CF_IMAGE_MODEL}")
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}",
               "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers,
                          json={"prompt": prompt, "num_steps": 6}, timeout=120)
        # نموذج SDXL-lightning يعيد الصورة مباشرة كـ bytes (PNG)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code == 200 and "image" in ctype:
            return r.content, None
        # بعض الاستجابات تأتي كـ JSON يحتوي على base64
        try:
            data = r.json()
            if data.get("success") and data.get("result", {}).get("image"):
                return base64.b64decode(data["result"]["image"]), None
            err = data.get("errors", [{}])[0].get("message", "خطأ غير معروف")
            return None, f"❌ {err}"
        except Exception:
            return None, "❌ تعذّر توليد الصورة (استجابة غير متوقعة)."
    except Exception as e:
        print(f"cf_image err: {e}")
        return None, "❌ تعذّر الاتصال بخدمة توليد الصور. حاول مرة أخرى."


# حالة انتظار إدخال نصي لأوامر الذكاء الاصطناعي: chat_id -> اسم الأمر
ai_waiting = {}


# ====================== الأوامر ======================
@bot.message_handler(commands=['start'])
def handle_start(message):
    if not require_sub(message):
        return
    reset_session(message.chat.id)
    bot.reply_to(message, WELCOME, reply_markup=main_menu(message.chat.id))


@bot.message_handler(commands=['cancel'])
def handle_cancel(message):
    ai_waiting.pop(message.chat.id, None)
    reset_session(message.chat.id)
    bot.reply_to(message, "تم الإلغاء ✅\nاكتب /start لاختيار أداة جديدة.")


@bot.message_handler(commands=['done'])
def handle_done(message):
    chat_id = message.chat.id
    sess = get_session(chat_id)
    if not sess or not sess.get("files"):
        bot.reply_to(message, "ما أرسلت أي ملف بعد 📎\nاكتب /start واختر أداة.")
        return
    if sess.get("timer"):
        sess["timer"].cancel()
    process(chat_id)


# ====================== أوامر الذكاء الاصطناعي ======================
@bot.message_handler(commands=['ai'])
def handle_ai(message):
    if not require_sub(message):
        return
    chat_id = message.chat.id
    reset_session(chat_id)  # نخرج من أي أداة نشطة
    arg = message.text.partition(" ")[2].strip()
    if arg:
        _run_ai_task(chat_id, "ai", arg)
    else:
        ai_waiting[chat_id] = "ai"
        bot.reply_to(message, "💬 اكتب سؤالك وسأجيبك:")


@bot.message_handler(commands=['translate'])
def handle_translate(message):
    if not require_sub(message):
        return
    chat_id = message.chat.id
    reset_session(chat_id)
    arg = message.text.partition(" ")[2].strip()
    if arg:
        _run_ai_task(chat_id, "translate", arg)
    else:
        ai_waiting[chat_id] = "translate"
        bot.reply_to(message, "🌐 أرسل النص الذي تريد ترجمته:")


@bot.message_handler(commands=['summarize'])
def handle_summarize(message):
    if not require_sub(message):
        return
    chat_id = message.chat.id
    reset_session(chat_id)
    arg = message.text.partition(" ")[2].strip()
    if arg:
        _run_ai_task(chat_id, "summarize", arg)
    else:
        ai_waiting[chat_id] = "summarize"
        bot.reply_to(message, "📝 أرسل النص الطويل الذي تريد تلخيصه:")


@bot.message_handler(commands=['image'])
def handle_image(message):
    if not require_sub(message):
        return
    chat_id = message.chat.id
    reset_session(chat_id)
    arg = message.text.partition(" ")[2].strip()
    if arg:
        _run_image_task(chat_id, arg)
    else:
        ai_waiting[chat_id] = "image"
        bot.reply_to(message, "🎨 صف الصورة التي تريد توليدها (بالإنجليزية أفضل):")


def _run_ai_task(chat_id, task, text):
    """ينفّذ مهمة نصية (دردشة/ترجمة/تلخيص) في خيط منفصل."""
    def worker():
        st = bot.send_message(chat_id, "⏳ جاري المعالجة...")
        try:
            if task == "ai":
                result = gemini_text(text)
            elif task == "translate":
                result = gemini_text(
                    text,
                    system="ترجم النص التالي. إن كان عربياً ترجمه للإنجليزية، "
                           "وإن كان بأي لغة أخرى ترجمه للعربية. أعطِ الترجمة فقط دون شرح.")
            elif task == "summarize":
                result = gemini_text(
                    text,
                    system="لخّص النص التالي بالعربية في نقاط واضحة ومختصرة.")
            else:
                result = "أمر غير معروف."
            # تلجرام يحدّ الرسالة بـ 4096 حرفاً
            bot.edit_message_text(result[:4000], chat_id, st.message_id)
        except Exception as e:
            print(f"ai task err {chat_id}: {e}")
            try:
                bot.edit_message_text("❌ حدث خطأ، حاول مرة أخرى.", chat_id, st.message_id)
            except:
                pass
    threading.Thread(target=worker).start()


def _run_image_task(chat_id, prompt):
    """يولّد صورة في خيط منفصل."""
    def worker():
        st = bot.send_message(chat_id, "🎨 جاري توليد الصورة...")
        try:
            img, err = cf_generate_image(prompt)
            if err:
                bot.edit_message_text(err, chat_id, st.message_id)
                return
            bot.send_photo(chat_id, img, caption="✅ تم توليد الصورة")
            try:
                bot.delete_message(chat_id, st.message_id)
            except:
                pass
        except Exception as e:
            print(f"image task err {chat_id}: {e}")
            try:
                bot.edit_message_text("❌ تعذّر توليد الصورة.", chat_id, st.message_id)
            except:
                pass
    threading.Thread(target=worker).start()


# ====================== أوامر الأدمن ======================
@bot.message_handler(commands=['stats'])
def handle_stats(message):
    if message.chat.id != ADMIN_ID:
        return  # تجاهل غير الأدمن بصمت
    with users_lock:
        count = len(known_users)
    bot.reply_to(
        message,
        f"📊 إحصائيات البوت\n\n"
        f"👥 عدد المستخدمين الكلي: {count}\n\n"
        f"⚠️ ملاحظة: العدد يُحفظ في ملف قد يُعاد ضبطه عند إعادة تشغيل الخادم "
        f"على الخطة المجانية."
    )


@bot.message_handler(commands=['users'])
def handle_users(message):
    """يعرض عدد مستخدمي البوت (للأدمن فقط)."""
    if message.chat.id != ADMIN_ID:
        return  # تجاهل غير الأدمن بصمت
    with users_lock:
        count = len(known_users)
    bot.reply_to(message, f"👤 عدد مستخدمي البوت: {count}")


# ====================== الأزرار ======================
@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    chat_id = call.message.chat.id

    if call.data == "check_sub":
        if chat_id == ADMIN_ID or check_sub(chat_id):
            bot.answer_callback_query(call.id, "تم التحقق ✅")
            try:
                bot.edit_message_text(WELCOME, chat_id, call.message.message_id,
                                      reply_markup=main_menu(chat_id))
            except:
                bot.send_message(chat_id, WELCOME, reply_markup=main_menu(chat_id))
        else:
            bot.answer_callback_query(call.id, "لم تشترك بعد ❌", show_alert=True)
        return

    if call.data.startswith("tool|"):
        tool = call.data.split("|")[1]
        if tool not in TOOLS:
            bot.answer_callback_query(call.id)
            return
        reset_session(chat_id, tool)
        bot.answer_callback_query(call.id)
        hint = TOOL_HINTS.get(tool, "أرسل ملفاتك الآن.")
        try:
            bot.edit_message_text(f"{TOOLS[tool]}\n\n{hint}", chat_id, call.message.message_id)
        except:
            bot.send_message(chat_id, f"{TOOLS[tool]}\n\n{hint}")
        return

    # اختيار صيغة التحويل
    if call.data.startswith("fmt|"):
        fmt = call.data.split("|")[1]
        sess = get_session(chat_id)
        if sess and sess.get("tool") == "convertimg" and sess.get("files"):
            sess["opt"] = fmt
            bot.answer_callback_query(call.id, f"الصيغة: {fmt}")
            process(chat_id)
        else:
            bot.answer_callback_query(call.id, "انتهت الجلسة، أعد الإرسال", show_alert=True)
        return

    bot.answer_callback_query(call.id)


# ====================== استقبال الصور ======================
@bot.message_handler(content_types=['photo'])
def on_photo(message):
    chat_id = message.chat.id
    if not require_sub(message):
        return
    sess = get_session(chat_id)
    if not sess:
        # لا توجد أداة نشطة → نحلّل الصورة بالذكاء الاصطناعي (Gemini)
        _analyze_photo(message)
        return

    tool = sess["tool"]
    if tool not in ("img2pdf", "compressimg", "convertimg", "zip"):
        bot.reply_to(message, "هذه الأداة لا تقبل صوراً. اكتب /start لاختيار أداة مناسبة.")
        return

    if len(sess["files"]) >= MAX_ITEMS:
        bot.reply_to(message, f"⚠️ وصلت للحد ({MAX_ITEMS}). اكتب /done.")
        return

    try:
        file_id = message.photo[-1].file_id
        info = bot.get_file(file_id)
        data = bot.download_file(info.file_path)
        path = f"f_{chat_id}_{len(sess['files'])}_{int(time.time()*1000)}.jpg"
        with open(path, "wb") as f:
            f.write(data)
        sess["files"].append(path)

        # أداة تحويل الصيغة تعمل على صورة واحدة وتعرض الخيارات مباشرة
        if tool == "convertimg":
            bot.send_message(chat_id, "اختر الصيغة المطلوبة 👇", reply_markup=fmt_markup())
            return

        arm_timer(chat_id)
        bot.reply_to(message, f"📷 تم الاستلام ({len(sess['files'])}). أرسل المزيد أو اكتب /done.")
    except Exception as e:
        print(f"photo err {chat_id}: {e}")
        bot.reply_to(message, "حدث خطأ أثناء الاستلام، حاول مرة أخرى.")


def fmt_markup():
    markup = InlineKeyboardMarkup(row_width=3)
    markup.row(
        InlineKeyboardButton("JPG", callback_data="fmt|JPEG"),
        InlineKeyboardButton("PNG", callback_data="fmt|PNG"),
        InlineKeyboardButton("WEBP", callback_data="fmt|WEBP"),
    )
    return markup


def _analyze_photo(message):
    """يحلّل صورة مرسلة (بدون أداة) عبر Gemini Vision في خيط منفصل."""
    chat_id = message.chat.id
    caption = (message.caption or "").strip()
    prompt = caption if caption else "صف هذه الصورة بالتفصيل بالعربية."

    def worker():
        st = bot.send_message(chat_id, "🔍 جاري تحليل الصورة...")
        try:
            info = bot.get_file(message.photo[-1].file_id)
            data = bot.download_file(info.file_path)
            result = gemini_vision(data, prompt)
            bot.edit_message_text(result[:4000], chat_id, st.message_id)
        except Exception as e:
            print(f"analyze photo err {chat_id}: {e}")
            try:
                bot.edit_message_text("❌ تعذّر تحليل الصورة.", chat_id, st.message_id)
            except:
                pass
    threading.Thread(target=worker).start()


# ====================== استقبال المستندات/الملفات ======================
@bot.message_handler(content_types=['document'])
def on_document(message):
    chat_id = message.chat.id
    if not require_sub(message):
        return
    sess = get_session(chat_id)
    if not sess:
        bot.reply_to(message, "اختر أداة أولاً 👇\nاكتب /start.", reply_markup=main_menu(chat_id))
        return

    tool = sess["tool"]
    doc = message.document
    fname = (doc.file_name or "file").lower()

    # تحقق من نوع الملف حسب الأداة
    is_pdf = fname.endswith(".pdf")
    if tool in ("mergepdf", "pdf2img", "splitpdf") and not is_pdf:
        bot.reply_to(message, "هذه الأداة تقبل ملفات PDF فقط 📄")
        return

    if len(sess["files"]) >= MAX_ITEMS:
        bot.reply_to(message, f"⚠️ وصلت للحد ({MAX_ITEMS}). اكتب /done.")
        return

    # تحقق من الحجم
    if doc.file_size and doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        bot.reply_to(message, f"❌ الملف أكبر من {MAX_FILE_SIZE_MB}MB.")
        return

    try:
        info = bot.get_file(doc.file_id)
        data = bot.download_file(info.file_path)
        # نحافظ على الامتداد الأصلي
        ext = os.path.splitext(fname)[1] or ".bin"
        path = f"f_{chat_id}_{len(sess['files'])}_{int(time.time()*1000)}{ext}"
        with open(path, "wb") as f:
            f.write(data)
        sess["files"].append(path)

        if tool == "splitpdf":
            # نطلب أرقام الصفحات بعد أول ملف
            try:
                reader = PdfReader(path)
                pages = len(reader.pages)
            except:
                pages = "?"
            sess["opt"] = "await_pages"
            bot.reply_to(message, f"📄 الملف يحتوي على {pages} صفحة.\n"
                                  "أرسل أرقام الصفحات المطلوبة، مثال:\n"
                                  "‎1,3,5  أو نطاق مثل ‎2-6")
            return

        arm_timer(chat_id)
        bot.reply_to(message, f"📎 تم الاستلام ({len(sess['files'])}). أرسل المزيد أو اكتب /done.")
    except Exception as e:
        print(f"doc err {chat_id}: {e}")
        bot.reply_to(message, "حدث خطأ أثناء الاستلام، حاول مرة أخرى.")


# ====================== استقبال النصوص (أرقام صفحات splitpdf) ======================
@bot.message_handler(func=lambda m: True, content_types=['text'])
def on_text(message):
    chat_id = message.chat.id
    text = message.text.strip()
    sess = get_session(chat_id)

    # حالة انتظار إدخال لأمر ذكاء اصطناعي
    if chat_id in ai_waiting:
        task = ai_waiting.pop(chat_id)
        if task == "image":
            _run_image_task(chat_id, text)
        else:
            _run_ai_task(chat_id, task, text)
        return

    # حالة انتظار أرقام صفحات لتقسيم PDF
    if sess and sess.get("tool") == "splitpdf" and sess.get("opt") == "await_pages":
        sess["opt"] = ("pages", text)
        process(chat_id)
        return

    bot.reply_to(message, "اكتب /start لاختيار أداة 🧰", reply_markup=main_menu(chat_id))


# ====================== التنفيذ ======================
def parse_pages(spec, total):
    """يحوّل '1,3,5' أو '2-6' إلى قائمة فهارس (تبدأ من صفر)."""
    result = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                for n in range(int(a), int(b) + 1):
                    if 1 <= n <= total:
                        result.append(n - 1)
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= total:
                result.append(n - 1)
    # إزالة التكرار مع الحفاظ على الترتيب
    seen = set()
    out = []
    for i in result:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def send_and_cleanup(chat_id, out_path, caption, status_id=None):
    try:
        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            msg = f"❌ الناتج أكبر من {MAX_FILE_SIZE_MB}MB، لا يمكن إرساله."
            if status_id:
                bot.edit_message_text(msg, chat_id, status_id)
            else:
                bot.send_message(chat_id, msg)
            return
        with open(out_path, "rb") as f:
            bot.send_document(chat_id, f,
                              visible_file_name=os.path.basename(out_path),
                              caption=caption)
        if status_id:
            try:
                bot.delete_message(chat_id, status_id)
            except:
                pass
    finally:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except:
            pass


def process(chat_id):
    sess = get_session(chat_id)
    if not sess or not sess.get("files"):
        return
    tool = sess["tool"]
    files = sess["files"]
    status = bot.send_message(chat_id, "⏳ جاري المعالجة...")
    sid = status.message_id
    ts = int(time.time())
    try:
        # ---------- صور ← PDF ----------
        if tool == "img2pdf":
            imgs = []
            for fp in files:
                im = Image.open(fp)
                if im.mode in ("RGBA", "P", "LA"):
                    im = im.convert("RGB")
                imgs.append(im)
            out = f"images_{chat_id}_{ts}.pdf"
            imgs[0].save(out, "PDF", save_all=True, append_images=imgs[1:])
            send_and_cleanup(chat_id, out, f"✅ تم تحويل {len(imgs)} صورة إلى PDF", sid)

        # ---------- دمج PDF ----------
        elif tool == "mergepdf":
            if len(files) < 2:
                bot.edit_message_text("أرسل ملفين PDF على الأقل للدمج 📚", chat_id, sid)
                return
            writer = PdfWriter()
            for fp in files:
                reader = PdfReader(fp)
                for page in reader.pages:
                    writer.add_page(page)
            out = f"merged_{chat_id}_{ts}.pdf"
            with open(out, "wb") as f:
                writer.write(f)
            send_and_cleanup(chat_id, out, f"✅ تم دمج {len(files)} ملف PDF", sid)

        # ---------- PDF ← صور ----------
        elif tool == "pdf2img":
            reader = PdfReader(files[0])
            count = 0
            produced = []
            for pi, page in enumerate(reader.pages):
                images = getattr(page, "images", [])
                for ii, img in enumerate(images):
                    p = f"page_{chat_id}_{pi}_{ii}_{ts}.png"
                    with open(p, "wb") as f:
                        f.write(img.data)
                    produced.append(p)
                    count += 1
            if not produced:
                bot.edit_message_text(
                    "تعذّر استخراج صور من هذا الـ PDF (قد يكون نصياً بدون صور مضمّنة).",
                    chat_id, sid)
            else:
                out = f"pdfimages_{chat_id}_{ts}.zip"
                with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                    for i, p in enumerate(produced, 1):
                        z.write(p, arcname=f"image_{i}.png")
                        try:
                            os.remove(p)
                        except:
                            pass
                send_and_cleanup(chat_id, out, f"✅ تم استخراج {count} صورة من الـ PDF", sid)

        # ---------- استخراج صفحات PDF ----------
        elif tool == "splitpdf":
            opt = sess.get("opt")
            if not (isinstance(opt, tuple) and opt[0] == "pages"):
                bot.edit_message_text("أرسل أرقام الصفحات المطلوبة أولاً.", chat_id, sid)
                return
            reader = PdfReader(files[0])
            total = len(reader.pages)
            idxs = parse_pages(opt[1], total)
            if not idxs:
                bot.edit_message_text("أرقام غير صحيحة. مثال: ‎1,3,5 أو ‎2-6", chat_id, sid)
                return
            writer = PdfWriter()
            for i in idxs:
                writer.add_page(reader.pages[i])
            out = f"split_{chat_id}_{ts}.pdf"
            with open(out, "wb") as f:
                writer.write(f)
            send_and_cleanup(chat_id, out, f"✅ تم استخراج {len(idxs)} صفحة", sid)

        # ---------- ضغط الصور ----------
        elif tool == "compressimg":
            produced = []
            for i, fp in enumerate(files, 1):
                im = Image.open(fp)
                if im.mode in ("RGBA", "P", "LA"):
                    im = im.convert("RGB")
                p = f"comp_{chat_id}_{i}_{ts}.jpg"
                im.save(p, "JPEG", quality=60, optimize=True)
                produced.append(p)
            if len(produced) == 1:
                send_and_cleanup(chat_id, produced[0], "✅ تم ضغط الصورة", sid)
            else:
                out = f"compressed_{chat_id}_{ts}.zip"
                with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                    for i, p in enumerate(produced, 1):
                        z.write(p, arcname=f"compressed_{i}.jpg")
                        try:
                            os.remove(p)
                        except:
                            pass
                send_and_cleanup(chat_id, out, f"✅ تم ضغط {len(files)} صورة", sid)

        # ---------- تحويل صيغة الصور ----------
        elif tool == "convertimg":
            fmt = sess.get("opt") or "PNG"
            im = Image.open(files[0])
            if fmt == "JPEG" and im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(fmt, "png")
            out = f"converted_{chat_id}_{ts}.{ext}"
            im.save(out, fmt)
            send_and_cleanup(chat_id, out, f"✅ تم التحويل إلى {ext.upper()}", sid)

        # ---------- ضغط ملفات ZIP ----------
        elif tool == "zip":
            out = f"archive_{chat_id}_{ts}.zip"
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for i, fp in enumerate(files, 1):
                    ext = os.path.splitext(fp)[1] or ""
                    z.write(fp, arcname=f"file_{i}{ext}")
            send_and_cleanup(chat_id, out, f"✅ تم ضغط {len(files)} ملف في ZIP", sid)

        else:
            bot.edit_message_text("أداة غير معروفة. اكتب /start.", chat_id, sid)

    except Exception as e:
        print(f"process err {chat_id} ({tool}): {e}")
        try:
            bot.edit_message_text("حدث خطأ أثناء المعالجة ❌\nتأكد من صحة الملفات وحاول مرة أخرى.",
                                  chat_id, sid)
        except:
            pass
    finally:
        reset_session(chat_id)
        # تنظيف أي مخلفات
        for pat in (f"f_{chat_id}_*", f"page_{chat_id}_*", f"comp_{chat_id}_*"):
            for p in glob.glob(pat):
                try:
                    os.remove(p)
                except:
                    pass


print("✅ بوت الأدوات يعمل الآن...")
bot.infinity_polling()
