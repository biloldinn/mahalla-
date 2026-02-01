import logging
from datetime import datetime
from typing import Dict, List, Optional
import json
import os
import threading
from flask import Flask
try:
    import pymongo
    from pymongo import MongoClient
except ImportError:
    pymongo = None

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)

# Log konfiguratsiyasi
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot tokeni va admin IDsi
BOT_TOKEN = "8425544423:AAHIuMlPG78s4Azlfcc5NxC0lgVWFeryBmM"
ADMIN_ID = 7985206085

# Holatlar (states)
MAHALLA_SELECT, PERSONAL_INFO, COMPLAINT_TEXT, COMPLAINT_PHOTO = range(4)
ADMIN_MAIN, ADD_MAHALLA, ADD_STAFF, REMOVE_STAFF, ADD_GROUP, SELECT_POSITION, DELETE_MAHALLA = range(4, 11)

# Mahalla hodimlari lavozimlar
STAFF_POSITIONS = [
    "Mahalla raisi (oqsoqol)",
    "Hokim yordamchisi",
    "Yoshlar yetakchisi",
    "Xotin-qizlar faoli",
    "Profilaktika inspektori",
    "Soliq bo'yicha mas'ul xodim",
    "Ijtimoiy xodim"
]

# Ma'lumotlarni saqlash (MongoDB va Lokal JSON)
class DataStorage:
    def __init__(self):
        self.mahallalar = {}
        self.users = {}
        self.complaints = []
        self.staff_members = {}
        
        # MongoDB sozlamalari
        self.mongo_uri = os.environ.get("MONGO_URI")
        self.db = None
        self.connected_to_mongo = False
        
        if self.mongo_uri and pymongo:
            try:
                self.client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
                # Ulanishni tekshirish
                self.client.server_info()
                self.db = self.client['mahalla_bot_db']
                self.connected_to_mongo = True
                logger.info("✅ MongoDB-ga muvaffaqiyatli ulanildi!")
            except Exception as e:
                logger.error(f"❌ MongoDB-ga ulanishda xato: {e}. Lokal fayldan foydalaniladi.")
    
    def save_data(self):
        data = {
            'mahallalar': self.mahallalar,
            'users': self.users,
            'complaints': self.complaints,
            'staff_members': self.staff_members
        }
        
        # 1. MongoDB-ga saqlash
        if self.connected_to_mongo:
            try:
                for key, value in data.items():
                    self.db['bot_data'].update_one(
                        {'_id': key},
                        {'$set': {'data': value}},
                        upsert=True
                    )
                # logger.debug("Ma'lumotlar MongoDB-ga saqlandi.")
            except Exception as e:
                logger.error(f"MongoDB-ga saqlashda xato: {e}")
        
        # 2. Lokal JSON-ga saqlash (zaxira uchun)
        try:
            temp_file = 'data.json.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if os.path.exists(temp_file):
                if os.name == 'nt' and os.path.exists('data.json'):
                    os.remove('data.json')
                os.rename(temp_file, 'data.json')
        except Exception as e:
            logger.error(f"Lokal saqlashda xato: {e}")

    def load_data(self):
        # 1. MongoDB-dan yuklash
        if self.connected_to_mongo:
            try:
                mongo_data = {}
                for item in self.db['bot_data'].find():
                    mongo_data[item['_id']] = item['data']
                
                if mongo_data:
                    self.mahallalar = mongo_data.get('mahallalar', {})
                    self.users = mongo_data.get('users', {})
                    self.complaints = mongo_data.get('complaints', [])
                    self.staff_members = mongo_data.get('staff_members', {})
                    
                    # Sanitizatsiya
                    for m_nomi in self.mahallalar:
                        if not isinstance(self.mahallalar[m_nomi].get('hodimlar'), dict):
                            self.mahallalar[m_nomi]['hodimlar'] = {}
                            
                    logger.info("✅ Ma'lumotlar MongoDB-dan yuklandi va sanitizatsiya qilindi.")
                    return
            except Exception as e:
                logger.error(f"MongoDB-dan yuklashda xato: {e}")

        # 2. Lokal JSON-dan yuklash
        if os.path.exists('data.json'):
            try:
                with open('data.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.mahallalar = data.get('mahallalar', {})
                    self.users = data.get('users', {})
                    self.complaints = data.get('complaints', [])
                    self.staff_members = data.get('staff_members', {})
                
                # Sanitizatsiya
                for m_nomi in self.mahallalar:
                    if not isinstance(self.mahallalar[m_nomi].get('hodimlar'), dict):
                        self.mahallalar[m_nomi]['hodimlar'] = {}
                
                logger.info("📂 Ma'lumotlar lokal fayldan yuklandi va sanitizatsiya qilindi.")
                
                # Agar MongoDB-ga endigina ulanilgan bo'lsa, lokal ma'lumotlarni migratsiya qilish
                if self.connected_to_mongo:
                    logger.info("Migratsiya boshlandi...")
                    self.save_data()
            except Exception as e:
                logger.error(f"Lokal yuklashda xato: {e}")

storage = DataStorage()
storage.load_data()

# Render uchun kichik veb-server (uxlab qolmasligi uchun)
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    try:
        # Render uchun port 10000 standart, lekin PORT o'zgaruvchisidan ham olish kerak
        port = int(os.environ.get("PORT", 10000))
        logger.info(f"Flask serveri {port}-portda ishlamoqda...")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask serverini ishga tushirishda xato: {e}")

# Xatoliklarni ushlab qolish
async def error_handler(update: Optional[object], context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Xatolik yuz berdi: {context.error}")
    # Agar xatolik update bilan bog'liq bo'lsa, foydalanuvchiga xabar berish
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Kutilmagan xatolik yuz berdi. Iltimos, keyinroq qayta urinib ko'ring."
            )
        except:
            pass

# Asosiy funksiyalar
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Adminni tekshirish
    if user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("➕ Mahalla qo'shish", callback_data='add_mahalla')],
            [InlineKeyboardButton("👥 Hodim qo'shish", callback_data='add_staff')],
            [InlineKeyboardButton("🗑️ Hodimni o'chirish", callback_data='remove_staff')],
            [InlineKeyboardButton("�️ Mahallani o'chirish", callback_data='delete_mahalla')],
            [InlineKeyboardButton("📊 Statistika", callback_data='stats')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "👋 Admin paneliga xush kelibsiz!\nQuyidagi amallardan birini tanlang:",
            reply_markup=reply_markup
        )
        return ADMIN_MAIN
    
    # Hodimni tekshirish (username bo'yicha)
    username = update.effective_user.username
    if username:
        for staff_username, staff_info in storage.staff_members.items():
            if staff_username.lower() == username.lower():
                # User ID ni saqlash (birinchi marta start bosganida)
                if not staff_info.get('user_id'):
                    staff_info['user_id'] = user_id
                    storage.save_data()
                
                keyboard = [
                    [InlineKeyboardButton("📨 Yangi shikoyatlar", callback_data='new_complaints')],
                    [InlineKeyboardButton("📋 Barcha shikoyatlar", callback_data='all_complaints')],
                    [InlineKeyboardButton("📊 Mening statistikam", callback_data='my_stats')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    f"👋 {staff_info['ism']} xush kelibsiz!\n"
                    f"Siz {staff_info['mahalla']} mahallasining {staff_info['lavozim']} etib tayinlandingiz.",
                    reply_markup=reply_markup
                )
                return ConversationHandler.END
    
    # Oddiy foydalanuvchi
    if storage.mahallalar:
        keyboard = []
        for mahalla in storage.mahallalar.keys():
            keyboard.append([InlineKeyboardButton(mahalla, callback_data=f"mahalla_{mahalla}")])
        
        # Veb-ilova tugmasini qo'shish
        keyboard.append([InlineKeyboardButton("🌐 Veb-ilova orqali yuborish", url="https://xavfsizhudud.vercel.app")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "👋 Mahalla shikoyat botiga xush kelibsiz!\n"
            "Iltimos, mahallangizni tanlang yoki veb-ilovadan foydalaning:",
            reply_markup=reply_markup
        )
        return MAHALLA_SELECT
    else:
        await update.message.reply_text(
            "⚠️ Hozircha mahalla mavjud emas. Iltimos, keyinroq urinib ko'ring."
        )
        return ConversationHandler.END

async def select_mahalla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    mahalla_nomi = query.data.replace("mahalla_", "")
    context.user_data['selected_mahalla'] = mahalla_nomi
    
    await query.edit_message_text(
        f"✅ {mahalla_nomi} mahallasi tanlandi.\n"
        f"Endi shikoyatingizni yozish uchun ismingiz, familiyangiz va telefon raqamingizni kiriting:\n\n"
        f"Format: Ism Familiya Telefon\n"
        f"Misol: Alijon Valiyev +998901234567"
    )
    return PERSONAL_INFO

async def get_personal_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    
    try:
        parts = user_input.split()
        if len(parts) < 3:
            raise ValueError
        
        ism_familiya = ' '.join(parts[:-1])
        telefon = parts[-1]
        
        # Foydalanuvchi ma'lumotlarini saqlash
        storage.users[str(user_id)] = {
            'ism': ism_familiya,
            'telefon': telefon,
            'username': update.effective_user.username,
            'mahalla': context.user_data['selected_mahalla']
        }
        
        # Hodimlarni ko'rsatish
        mahalla_nomi = context.user_data['selected_mahalla']
        hodimlar = storage.mahallalar.get(mahalla_nomi, {}).get('hodimlar', {})
        
        keyboard = []
        for lavozim, username in hodimlar.items():
            keyboard.append([
                InlineKeyboardButton(
                    lavozim,
                    callback_data=f"staff_{username}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "✅ Ma'lumotlaringiz qabul qilindi.\n"
            "Endi shikoyatingizni qaysi hodimga yo'naltirmoqchisiz?",
            reply_markup=reply_markup
        )
        
        return COMPLAINT_TEXT
        
    except ValueError:
        await update.message.reply_text(
            "❌ Noto'g'ri format. Iltimos, quyidagi formatda kiriting:\n"
            "Ism Familiya Telefon\n"
            "Misol: Alijon Valiyev +998901234567"
        )
        return PERSONAL_INFO

async def select_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    staff_username = query.data.replace("staff_", "")
    context.user_data['selected_staff'] = staff_username
    
    await query.edit_message_text(
        "✅ Hodim tanlandi.\n"
        "Endi shikoyatingizni batafsil yozing:"
    )
    return COMPLAINT_PHOTO

async def get_complaint_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    complaint_text = update.message.text
    context.user_data['complaint_text'] = complaint_text
    
    keyboard = [
        [InlineKeyboardButton("📎 Rasm qo'shish", callback_data='add_photo')],
        [InlineKeyboardButton("✅ Rasm siz yuborish", callback_data='no_photo')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Shikoyatingiz qabul qilindi. Rasm qo'shmoqchimisiz?",
        reply_markup=reply_markup
    )
    return COMPLAINT_PHOTO

async def handle_complaint_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    user_info = storage.users.get(str(user_id))
    staff_username = context.user_data['selected_staff']
    complaint_text = context.user_data['complaint_text']
    mahalla_nomi = context.user_data['selected_mahalla']
    
    # Shikoyatni yaratish
    complaint_id = len(storage.complaints) + 1
    complaint = {
        'id': complaint_id,
        'user_id': user_id,
        'user_info': user_info,
        'staff_username': staff_username,
        'mahalla': mahalla_nomi,
        'text': complaint_text,
        'photo': None,
        'status': 'yangi',
        'timestamp': datetime.now().isoformat()
    }
    
    if query.data == 'add_photo':
        await query.edit_message_text("📤 Iltimos, rasm yuboring:")
        context.user_data['waiting_for_photo'] = True
        return COMPLAINT_PHOTO
    
    # Shikoyatni saqlash
    storage.complaints.append(complaint)
    storage.save_data()
    
    # Hodimga xabar yuborish
    staff_info = storage.staff_members.get(staff_username)
    if staff_info and staff_info.get('user_id'):
        message_text = f"📨 YANGI SHIKOYAT #{complaint_id}\n\n"
        message_text += f"👤 Fuqaro: {user_info['ism']}\n"
        message_text += f"📞 Telefon: {user_info['telefon']}\n"
        if user_info.get('username'):
            message_text += f"📱 Telegram: @{user_info['username']}\n"
        message_text += f"🏘️ Mahalla: {mahalla_nomi}\n"
        message_text += f"📝 Shikoyat: {complaint_text}\n\n"
        message_text += f"⏰ Vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        try:
            await context.bot.send_message(
                chat_id=staff_info['user_id'],
                text=message_text
            )
        except:
            pass
    
    # Mahfiy guruhga yuborish
    group_username = storage.mahallalar.get(mahalla_nomi, {}).get('group_username')
    if group_username and staff_info and staff_info.get('user_id'):
        message_text_group = f"📨 YANGI SHIKOYAT #{complaint_id}\n\n"
        message_text_group += f"👤 Fuqaro: {user_info['ism']}\n"
        message_text_group += f"📞 Telefon: {user_info['telefon']}\n"
        if user_info.get('username'):
            message_text_group += f"📱 Telegram: @{user_info['username']}\n"
        message_text_group += f"🏘️ Mahalla: {mahalla_nomi}\n"
        message_text_group += f"�‍💼 Mas'ul hodim: {staff_info['ism']} ({staff_info['lavozim']})\n"
        message_text_group += f"📝 Shikoyat: {complaint_text}"
        
        try:
            await context.bot.send_message(
                chat_id=f"@{group_username}",
                text=message_text_group
            )
        except Exception as e:
            logger.error(f"Guruhga xabar yuborishda xato: {e}")
    
    await query.edit_message_text(
        "✅ Shikoyatingiz qabul qilindi va hodimga yuborildi.\n"
        "Hodim shikoyatingizni ko'rganida sizga xabar beramiz.\n\n"
        "⚠️ Diqqat: Yolg'on ma'lumot bergan holda shikoyat qilish qonunga ziddir!"
    )
    
    # Foydalanuvchiga tasdiqlash
    context.user_data.clear()
    return ConversationHandler.END

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_for_photo'):
        photo = update.message.photo[-1].file_id
        
        user_id = update.effective_user.id
        user_info = storage.users.get(str(user_id))
        staff_username = context.user_data['selected_staff']
        complaint_text = context.user_data['complaint_text']
        mahalla_nomi = context.user_data['selected_mahalla']
        
        # Shikoyatni yaratish
        complaint_id = len(storage.complaints) + 1
        complaint = {
            'id': complaint_id,
            'user_id': user_id,
            'user_info': user_info,
            'staff_username': staff_username,
            'mahalla': mahalla_nomi,
            'text': complaint_text,
            'photo': photo,
            'status': 'yangi',
            'timestamp': datetime.now().isoformat()
        }
        
        # Shikoyatni saqlash
        storage.complaints.append(complaint)
        storage.save_data()
        
        # Hodimga xabar yuborish
        staff_info = storage.staff_members.get(staff_username)
        if staff_info and staff_info.get('user_id'):
            message_text = f"📨 YANGI SHIKOYAT #{complaint_id}\n\n"
            message_text += f"👤 Fuqaro: {user_info['ism']}\n"
            message_text += f"📞 Telefon: {user_info['telefon']}\n"
            if user_info.get('username'):
                message_text += f"📱 Telegram: @{user_info['username']}\n"
            message_text += f"🏘️ Mahalla: {mahalla_nomi}\n"
            message_text += f"📝 Shikoyat: {complaint_text}"
            
            try:
                await context.bot.send_photo(
                    chat_id=staff_info['user_id'],
                    photo=photo,
                    caption=message_text
                )
            except:
                pass
        
        # Mahfiy guruhga yuborish (rasm bilan)
        group_username = storage.mahallalar.get(mahalla_nomi, {}).get('group_username')
        if group_username and staff_info and staff_info.get('user_id'):
            caption_group = f"📨 YANGI SHIKOYAT #{complaint_id}\n\n"
            caption_group += f"👤 Fuqaro: {user_info['ism']}\n"
            caption_group += f"📞 Telefon: {user_info['telefon']}\n"
            if user_info.get('username'):
                caption_group += f"📱 Telegram: @{user_info['username']}\n"
            caption_group += f"🏘️ Mahalla: {mahalla_nomi}\n"
            caption_group += f"�‍💼 Mas'ul hodim: {staff_info['ism']} ({staff_info['lavozim']})\n"
            caption_group += f"📝 Shikoyat: {complaint_text}"
            
            try:
                await context.bot.send_photo(
                    chat_id=f"@{group_username}",
                    photo=photo,
                    caption=caption_group
                )
            except Exception as e:
                logger.error(f"Guruhga rasm yuborishda xato: {e}")
        
        await update.message.reply_text(
            "✅ Shikoyatingiz qabul qilindi va hodimga yuborildi.\n"
            "Hodim shikoyatingizni ko'rganida sizga xabar beramiz.\n\n"
            "⚠️ Diqqat: Yolg'on ma'lumot bergan holda shikoyat qilish qonunga ziddir!"
        )
        
        context.user_data.clear()
        return ConversationHandler.END
    
    return COMPLAINT_PHOTO

# Admin funksiyalari
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'add_mahalla':
        await query.edit_message_text(
            "➕ Yangi mahalla qo'shish uchun nomini kiriting:"
        )
        return ADD_MAHALLA
    
    elif query.data == 'add_staff':
        if not storage.mahallalar:
            await query.edit_message_text(
                "⚠️ Avval mahalla qo'shishingiz kerak!"
            )
            return ADMIN_MAIN
        
        keyboard = []
        for mahalla in storage.mahallalar.keys():
            keyboard.append([InlineKeyboardButton(mahalla, callback_data=f"staffmahalla_{mahalla}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "👥 Hodim qo'shish uchun mahallani tanlang:",
            reply_markup=reply_markup
        )
        return ADD_STAFF
    
    elif query.data == 'remove_staff':
        if not storage.staff_members:
            await query.edit_message_text("⚠️ Hozircha hodimlar mavjud emas!")
            return ADMIN_MAIN
        
        keyboard = []
        for username, info in storage.staff_members.items():
            keyboard.append([
                InlineKeyboardButton(
                    f"{info['ism']} (@{username}) - {info['mahalla']} - {info['lavozim']}",
                    callback_data=f"removestaff_{username}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🗑️ O'chirish uchun hodimni tanlang:",
            reply_markup=reply_markup
        )
        return REMOVE_STAFF
    
    elif query.data == 'delete_mahalla':
        if not storage.mahallalar:
            await query.edit_message_text("⚠️ Hozircha mahallalar mavjud emas!")
            return ADMIN_MAIN
        
        keyboard = []
        for mahalla in storage.mahallalar.keys():
            keyboard.append([InlineKeyboardButton(mahalla, callback_data=f"delmahalla_{mahalla}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "�️ O'chirish uchun mahallani tanlang:",
            reply_markup=reply_markup
        )
        return DELETE_MAHALLA
    
    elif query.data == 'stats':
        # Shikoyatlar statistikasi
        jami = len(storage.complaints)
        korilgan = len([c for c in storage.complaints if c['status'] == 'ko\'rilgan'])
        bajarildi = len([c for c in storage.complaints if c['status'] == 'bajarildi'])
        yangi = len([c for c in storage.complaints if c['status'] == 'yangi'])
        
        stats_text = "📊 BOT STATISTIKASI\n\n"
        stats_text += f"📨 Jami murojatlar: {jami}\n"
        stats_text += f"🆕 Yangi murojaatlar: {yangi}\n"
        stats_text += f"👁️ Ko'rilayotgan murojaatlar: {korilgan}\n"
        stats_text += f"✅ Bajarilgan ishlar: {bajarildi}\n\n"
        stats_text += f"🏘️ Mahallalar: {len(storage.mahallalar)}\n"
        stats_text += f"👥 Hodimlar: {len(storage.staff_members)}\n"
        stats_text += f"👤 Foydalanuvchilar: {len(storage.users)}\n\n"
        
        # Hodimlar ro'yxati
        if storage.staff_members:
            stats_text += "👥 HODIMLAR:\n"
            for username, info in storage.staff_members.items():
                stats_text += f"\n• {info['ism']}\n"
                stats_text += f"  Lavozim: {info['lavozim']}\n"
                stats_text += f"  Mahalla: {info['mahalla']}\n"
                stats_text += f"  Telefon: {info['telefon']}\n"
                stats_text += f"  Username: @{username}\n"
        
        await query.edit_message_text(stats_text)
        return ADMIN_MAIN
    
    elif query.data == 'admin_main':
        return await admin_main_menu(update, context)

async def add_mahalla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mahalla_nomi = update.message.text.strip()
    
    if mahalla_nomi not in storage.mahallalar:
        storage.mahallalar[mahalla_nomi] = {
            'hodimlar': {},
            'group_id': None
        }
        storage.save_data()
        
        await update.message.reply_text(f"✅ '{mahalla_nomi}' mahallasi qo'shildi!")
    else:
        await update.message.reply_text("⚠️ Bu mahalla allaqachon mavjud!")
    
    return await admin_main_menu(update, context)

async def select_mahalla_for_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    mahalla_nomi = query.data.replace("staffmahalla_", "")
    context.user_data['staff_mahalla'] = mahalla_nomi
    
    # Qo'shilgan hodimlarni olish
    hodimlar = storage.mahallalar.get(mahalla_nomi, {}).get('hodimlar', {})
    qoshilgan_lavozimlar = set(hodimlar.keys())
    
    # Qolgan lavozimlarni ko'rsatish
    qolgan_lavozimlar = [pos for pos in STAFF_POSITIONS if pos not in qoshilgan_lavozimlar]
    
    if not qolgan_lavozimlar:
        await query.edit_message_text(
            f"⚠️ {mahalla_nomi} mahallasiga barcha hodimlar qo'shilgan!\n"
            "Yangi hodim qo'shish uchun avval biror hodimni o'chiring."
        )
        return ADMIN_MAIN
    
    # Lavozimlarni tugmalar bilan ko'rsatish
    keyboard = []
    for position in qolgan_lavozimlar:
        keyboard.append([InlineKeyboardButton(position, callback_data=f"position_{position}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        f"🏘️ Mahalla: {mahalla_nomi}\n"
        f"📊 Qo'shilgan: {len(qoshilgan_lavozimlar)}/7\n"
        f"📋 Qolgan: {len(qolgan_lavozimlar)}/7\n\n"
        "Hodim lavozimini tanlang:",
        reply_markup=reply_markup
    )
    return SELECT_POSITION

async def select_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    lavozim = query.data.replace("position_", "")
    context.user_data['staff_lavozim'] = lavozim
    
    await query.edit_message_text(
        f"✅ Lavozim tanlandi: {lavozim}\n\n"
        "Hodim ma'lumotlarini kiriting:\n\n"
        "Format: Ism Familiya Telefon @username\n"
        "Misol: Anvar Xolmirzayev +998901234567 @anvar_xolmirzayev\n\n"
        "⚠️ Username @ belgisi bilan boshlanishi kerak!"
    )
    return ADD_STAFF

async def add_staff_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        if len(parts) < 3:
            raise ValueError("Kamida 3 ta qism bo'lishi kerak!")
        
        username = parts[-1]
        telefon = parts[-2]
        ism_familiya = ' '.join(parts[:-2])
        
        if not username.startswith('@'):
            raise ValueError("Username @ belgisi bilan boshlanishi kerak!")
        
        username = username[1:]  # @ belgisini olib tashlash
        lavozim = context.user_data['staff_lavozim']
        mahalla_nomi = context.user_data['staff_mahalla']
        
        # Hodimni saqlash
        storage.staff_members[username] = {
            'ism': ism_familiya,
            'telefon': telefon,
            'lavozim': lavozim,
            'mahalla': mahalla_nomi,
            'user_id': None
        }
        
        # Mahallaga hodimni qo'shish
        if 'hodimlar' not in storage.mahallalar[mahalla_nomi]:
            storage.mahallalar[mahalla_nomi]['hodimlar'] = {}
        
        storage.mahallalar[mahalla_nomi]['hodimlar'][lavozim] = username
        storage.save_data()
        
        await update.message.reply_text(
            f"✅ Hodim qo'shildi!\n"
            f"Ism: {ism_familiya}\n"
            f"Lavozim: {lavozim}\n"
            f"Mahalla: {mahalla_nomi}\n"
            f"Username: @{username}\n\n"
            f"⚠️ Hodim botga /start buyrug'ini yuborishi kerak!"
        )
        
        # Qolgan hodimlarni tekshirish
        hodimlar = storage.mahallalar[mahalla_nomi]['hodimlar']
        qoshilgan_soni = len(hodimlar)
        
        if qoshilgan_soni < 7:
            qolgan_lavozimlar = [pos for pos in STAFF_POSITIONS if pos not in hodimlar.keys()]
            
            keyboard = []
            for position in qolgan_lavozimlar:
                keyboard.append([InlineKeyboardButton(position, callback_data=f"position_{position}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"📊 {mahalla_nomi}: {qoshilgan_soni}/7 hodim\n\n"
                "Keyingi hodim lavozimini tanlang:",
                reply_markup=reply_markup
            )
            return SELECT_POSITION
        else:
            await update.message.reply_text(
                f"🎉 {mahalla_nomi} mahallasiga 7 ta hodim qo'shildi!\n"
                "Admin panelga qaytilmoqda..."
            )
            return await admin_main_menu(update, context)
        
    except ValueError as e:
        error_msg = str(e) if str(e) else "Noto'g'ri format"
        await update.message.reply_text(
            f"❌ {error_msg}\n\n"
            "Format: Ism Familiya Telefon @username\n"
            "Misol: Anvar Xolmirzayev +998901234567 @anvar_xolmirzayev"
        )
        return ADD_STAFF

async def remove_staff_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    username = query.data.replace("removestaff_", "")
    
    if username in storage.staff_members:
        staff_info = storage.staff_members[username]
        mahalla_nomi = staff_info['mahalla']
        lavozim = staff_info['lavozim']
        
        # Mahalladan o'chirish
        hodimlar = storage.mahallalar[mahalla_nomi].get('hodimlar', {})
        if isinstance(hodimlar, dict):
            if lavozim in hodimlar:
                del storage.mahallalar[mahalla_nomi]['hodimlar'][lavozim]
        elif isinstance(hodimlar, list):
            if username in hodimlar:
                storage.mahallalar[mahalla_nomi]['hodimlar'].remove(username)
        
        # Umumiy ro'yxatdan o'chirish
        if username in storage.staff_members:
            del storage.staff_members[username]
        storage.save_data()
        
        await query.edit_message_text(f"✅ Hodim muvaffaqiyatli o'chirildi!")
    else:
        await query.edit_message_text("❌ Hodim topilmadi!")
    
    return await admin_main_menu(update, context)

async def delete_mahalla_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    mahalla_nomi = query.data.replace("delmahalla_", "")
    
    if mahalla_nomi in storage.mahallalar:
        # Mahallaga tegishli hodimlarni o'chirish
        hodimlar = storage.mahallalar[mahalla_nomi].get('hodimlar', {})
        if isinstance(hodimlar, dict):
            for lavozim, username_key in hodimlar.items():
                if username_key in storage.staff_members:
                    del storage.staff_members[username_key]
        elif isinstance(hodimlar, list):
            for username_key in hodimlar:
                if username_key in storage.staff_members:
                    del storage.staff_members[username_key]
        
        # Mahallani o'chirish
        del storage.mahallalar[mahalla_nomi]
        storage.save_data()
        
        await query.edit_message_text(
            f"✅ '{mahalla_nomi}' mahallasi va unga tegishli barcha hodimlar o'chirildi!"
        )
    else:
        await query.edit_message_text("❌ Mahalla topilmadi!")
    
    return await admin_main_menu(update, context)

async def select_mahalla_for_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    mahalla_nomi = query.data.replace("groupmahalla_", "")
    context.user_data['group_mahalla'] = mahalla_nomi
    
    await query.edit_message_text(
        f"🏘️ Mahalla: {mahalla_nomi}\n\n"
        "Endi mahfiy guruh ID sini kiriting:\n\n"
        "Guruh ID sini olish uchun @raw_data_bot dan foydalaning."
    )
    return ADD_GROUP

async def add_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        group_id = int(update.message.text.strip())
        mahalla_nomi = context.user_data['group_mahalla']
        
        storage.mahallalar[mahalla_nomi]['group_id'] = group_id
        storage.save_data()
        
        await update.message.reply_text(
            f"✅ {mahalla_nomi} mahallasi uchun guruh qo'shildi!\n"
            f"Guruh ID: {group_id}\n\n"
            "Endi bu guruhga barcha shikoyatlar yuboriladi."
        )
        
        return await admin_main_menu(update, context)
        
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri guruh ID si. Iltimos, raqam kiriting.")
        return ADD_GROUP

async def admin_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
    else:
        message = update.message
    
    keyboard = [
        [InlineKeyboardButton("➕ Mahalla qo'shish", callback_data='add_mahalla')],
        [InlineKeyboardButton("👥 Hodim qo'shish", callback_data='add_staff')],
        [InlineKeyboardButton("🗑️ Hodimni o'chirish", callback_data='remove_staff')],
        [InlineKeyboardButton("�️ Mahallani o'chirish", callback_data='delete_mahalla')],
        [InlineKeyboardButton("📊 Statistika", callback_data='stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(
        "🏠 Admin paneli:\nQuyidagi amallardan birini tanlang:",
        reply_markup=reply_markup
    )
    return ADMIN_MAIN

# Hodim funksiyalari
async def staff_new_complaints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    staff_id = str(user_id)
    
    # Hodimga tegishli yangi shikoyatlarni olish
    username = update.effective_user.username
    new_complaints = [
        c for c in storage.complaints 
        if c.get('staff_username') == username and c['status'] == 'yangi'
    ]
    
    if not new_complaints:
        await query.edit_message_text("📭 Yangi shikoyatlar yo'q.")
        return ConversationHandler.END
    
    # Birinchi shikoyatni ko'rsatish
    complaint = new_complaints[0]
    user_info = complaint['user_info']
    
    message_text = f"📨 SHIKOYAT #{complaint['id']}\n\n"
    message_text += f"👤 Fuqaro: {user_info['ism']}\n"
    message_text += f"📞 Telefon: {user_info['telefon']}\n"
    if user_info.get('username'):
        message_text += f"📱 Telegram: @{user_info['username']}\n"
    message_text += f"🏘️ Mahalla: {complaint['mahalla']}\n"
    message_text += f"📝 Shikoyat: {complaint['text']}\n\n"
    message_text += f"⏰ Vaqt: {complaint['timestamp'][:19]}"
    
    # Shikoyat ko'rilgan deb belgilash va foydalanuvchiga xabar
    if complaint['status'] == 'yangi':
        complaint['status'] = 'ko\'rilgan'
        storage.save_data()
        
        # Foydalanuvchiga avtomatik xabar
        try:
            await context.bot.send_message(
                chat_id=complaint['user_id'],
                text=f"👁️ #{complaint['id']} raqamli shikoyatingiz hodim tomonidan ko'rildi.\n"
                     f"Hodim masalani ko'rib chiqmoqda."
            )
        except:
            pass
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Bajarildi", callback_data=f"complete_{complaint['id']}"),
            InlineKeyboardButton("🗑️ O'chirish", callback_data=f"delete_{complaint['id']}")
        ]
    ]
    
    if complaint.get('photo'):
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=complaint['photo'],
                caption=message_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await query.delete_message()
        except:
            await query.edit_message_text(
                message_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    else:
        await query.edit_message_text(
            message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    context.user_data['current_complaint_id'] = complaint['id']
    return ConversationHandler.END

async def mark_complaint_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    complaint_id = int(query.data.replace("complete_", ""))
    
    # Shikoyatni bajarildi deb belgilash
    for complaint in storage.complaints:
        if complaint['id'] == complaint_id:
            complaint['status'] = 'bajarildi'
            
            # Foydalanuvchiga xabar
            try:
                await context.bot.send_message(
                    chat_id=complaint['user_id'],
                    text=f"✅ #{complaint_id} raqamli shikoyatingiz bajarildi!\n"
                         f"Murojatingiz uchun rahmat!"
                )
            except:
                pass
            
            break
    
    storage.save_data()
    await query.edit_message_text("✅ Shikoyat bajarildi deb belgilandi.")
    return ConversationHandler.END

async def delete_complaint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    complaint_id = int(query.data.replace("delete_", ""))
    
    # Shikoyatni o'chirish
    storage.complaints = [c for c in storage.complaints if c['id'] != complaint_id]
    storage.save_data()
    
    await query.edit_message_text("🗑️ Shikoyat o'chirildi.")
    return ConversationHandler.END

async def staff_all_complaints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Bu bo'lim ustida ish olib borilmoqda...", show_alert=True)

async def staff_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Bu bo'lim ustida ish olib borilmoqda...", show_alert=True)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Amal bekor qilindi.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def main():
    import time
    
    # Veb-serverni alohida thread'da ishga tushirish (Render uchun)
    threading.Thread(target=run_flask, daemon=True).start()
    
    while True:
        try:
            # Botni yaratish
            application = Application.builder().token(BOT_TOKEN).build()
            
            # Birlashtirilgan main conversation handler
            main_conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler('start', start),
                    CallbackQueryHandler(admin_panel, pattern='^(add_mahalla|add_staff|remove_staff|delete_mahalla|stats|admin_main)$')
                ],
                states={
                    MAHALLA_SELECT: [CallbackQueryHandler(select_mahalla, pattern='^mahalla_')],
                    PERSONAL_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_personal_info)],
                    COMPLAINT_TEXT: [
                        CallbackQueryHandler(select_staff, pattern='^staff_'),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, get_complaint_text)
                    ],
                    COMPLAINT_PHOTO: [
                        CallbackQueryHandler(handle_complaint_photo, pattern='^(add_photo|no_photo)$'),
                        MessageHandler(filters.PHOTO, handle_photo)
                    ],
                    ADMIN_MAIN: [CallbackQueryHandler(admin_panel, pattern='^(add_mahalla|add_staff|remove_staff|delete_mahalla|stats|admin_main)$')],
                    ADD_MAHALLA: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mahalla)],
                    SELECT_POSITION: [CallbackQueryHandler(select_position, pattern='^position_')],
                    ADD_STAFF: [
                        CallbackQueryHandler(select_mahalla_for_staff, pattern='^staffmahalla_'),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, add_staff_member)
                    ],
                    REMOVE_STAFF: [CallbackQueryHandler(remove_staff_member, pattern='^removestaff_')],
                    DELETE_MAHALLA: [CallbackQueryHandler(delete_mahalla_confirm, pattern='^delmahalla_')],
                    ADD_GROUP: [
                        CallbackQueryHandler(select_mahalla_for_group, pattern='^groupmahalla_'),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_id)
                    ]
                },
                fallbacks=[CommandHandler('cancel', cancel), CommandHandler('start', start)]
            )
            
            # Hodim uchun handlerlar
            staff_handler = CallbackQueryHandler(staff_new_complaints, pattern='^new_complaints$')
            staff_all_handler = CallbackQueryHandler(staff_all_complaints, pattern='^all_complaints$')
            staff_stats_handler = CallbackQueryHandler(staff_stats, pattern='^my_stats$')
            complete_handler = CallbackQueryHandler(mark_complaint_complete, pattern='^complete_')
            delete_complaint_handler = CallbackQueryHandler(delete_complaint, pattern='^delete_')
            
            # Handlerlarni qo'shish
            application.add_handler(main_conv_handler)
            application.add_handler(staff_handler)
            application.add_handler(staff_all_handler)
            application.add_handler(staff_stats_handler)
            application.add_handler(complete_handler)
            application.add_handler(delete_complaint_handler)
            
            # Xatolik handlerini qo'shish
            application.add_error_handler(error_handler)
            
            # Botni ishga tushirish
            logger.info("Bot polling ishga tushirildi...")
            application.run_polling(drop_pending_updates=True)
            
        except Exception as e:
            logger.error(f"Botda jiddiy xatolik (main loop): {e}. 10 soniyadan so'ng qayta uriniladi...")
            time.sleep(10)

if __name__ == '__main__':
    main()
