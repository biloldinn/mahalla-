import logging
from datetime import datetime
from typing import Dict, List, Optional
import json
import os

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

# Ma'lumotlarni saqlash
class DataStorage:
    def __init__(self):
        self.mahallalar = {}  # mahalla_nomi: {hodimlar: {lavozim: username}, group_id: None}
        self.users = {}  # user_id: {ism: "", tel: "", mahalla: ""}
        self.complaints = []  # shikoyatlar ro'yxati
        self.staff_members = {}  # username: {ism: "", mahalla: "", lavozim: "", tel: "", user_id: None}
    
    def save_data(self):
        data = {
            'mahallalar': self.mahallalar,
            'users': self.users,
            'complaints': self.complaints,
            'staff_members': self.staff_members
        }
        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def load_data(self):
        try:
            with open('data.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.mahallalar = data.get('mahallalar', {})
                self.users = data.get('users', {})
                self.complaints = data.get('complaints', [])
                self.staff_members = data.get('staff_members', {})
        except FileNotFoundError:
            pass

storage = DataStorage()
storage.load_data()

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
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "👋 Mahalla shikoyat botiga xush kelibsiz!\n"
            "Iltimos, o'zingizning mahallangizni tanlang:",
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
        stats_text = "📊 BOT STATISTIKASI\n\n"
        stats_text += f"🏘️ Mahallalar soni: {len(storage.mahallalar)}\n"
        stats_text += f"👥 Hodimlar soni: {len(storage.staff_members)}\n"
        stats_text += f"📨 Shikoyatlar soni: {len(storage.complaints)}\n"
        stats_text += f"👤 Ro'yxatdan o'tganlar: {len(storage.users)}\n\n"
        
        # Mahalla bo'yicha statistika
        for mahalla, data in storage.mahallalar.items():
            hodimlar_soni = len(data.get('hodimlar', []))
            stats_text += f"{mahalla}: {hodimlar_soni} hodim\n"
        
        await query.edit_message_text(stats_text)
        return ADMIN_MAIN

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
        "Endi hodimning ma'lumotlarini quyidagi formatda kiriting:\n\n"
        "Ism Familiya Telefon @username @guruh_username\n\n"
        "Misol 1: Anvar Xolmirzayev +998901234567 @anvar_xolmirzayev @mahalla_guruh\n"
        "Misol 2: Anvar Xolmirzayev +998901234567 @anvar_xolmirzayev\n\n"
        "⚠️ Diqqat:\n"
        "- Username @ belgisi bilan boshlanishi kerak!\n"
        "- Guruh username ixtiyoriy (yozsangiz ham bo'ladi, yozmasangiz ham)\n"
        "- Agar guruh username yozsangiz, botni guruhga admin qiling!"
    )
    return ADD_STAFF

async def add_staff_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        if len(parts) < 3:
            raise ValueError("Kamida 3 ta qism bo'lishi kerak!")
        
        # Guruh username borligini tekshirish
        group_username = None
        if len(parts) >= 4 and parts[-1].startswith('@'):
            group_username = parts[-1][1:]  # @ belgisini olib tashlash
            username = parts[-2]
            telefon = parts[-3]
            ism_familiya = ' '.join(parts[:-3])
        else:
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
            'user_id': None  # Hodim /start bosganida to'ldiriladi
        }
        
        # Mahallaga hodimni qo'shish
        if 'hodimlar' not in storage.mahallalar[mahalla_nomi]:
            storage.mahallalar[mahalla_nomi]['hodimlar'] = {}
        
        storage.mahallalar[mahalla_nomi]['hodimlar'][lavozim] = username
        
        # Guruh username ni saqlash
        if group_username:
            storage.mahallalar[mahalla_nomi]['group_username'] = group_username
        
        storage.save_data()
        
        response_text = f"✅ Hodim muvaffaqiyatli qo'shildi!\n"
        response_text += f"Ism: {ism_familiya}\n"
        response_text += f"Lavozim: {lavozim}\n"
        response_text += f"Mahalla: {mahalla_nomi}\n"
        response_text += f"Username: @{username}\n"
        
        if group_username:
            response_text += f"Guruh: @{group_username}\n\n"
            response_text += f"⚠️ DIQQAT: Botni @{group_username} guruhiga admin qiling!\n"
        
        response_text += f"\n⚠️ Hodim botga /start buyrug'ini yuborishi kerak!"
        
        await update.message.reply_text(response_text)
        
        # Qolgan hodimlarni tekshirish
        hodimlar = storage.mahallalar[mahalla_nomi]['hodimlar']
        qoshilgan_soni = len(hodimlar)
        
        if qoshilgan_soni < 7:
            # Davom etish
            qolgan_lavozimlar = [pos for pos in STAFF_POSITIONS if pos not in hodimlar.keys()]
            
            keyboard = []
            for position in qolgan_lavozimlar:
                keyboard.append([InlineKeyboardButton(position, callback_data=f"position_{position}")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"📊 {mahalla_nomi} mahallasi:\n"
                f"✅ Qo'shilgan: {qoshilgan_soni}/7\n"
                f"📋 Qolgan: {7 - qoshilgan_soni}/7\n\n"
                "Keyingi hodim lavozimini tanlang:",
                reply_markup=reply_markup
            )
            return SELECT_POSITION
        else:
            # 7 ta hodim to'ldi
            await update.message.reply_text(
                f"🎉 {mahalla_nomi} mahallasiga barcha 7 ta hodim qo'shildi!\n\n"
                "Admin panelga qaytilmoqda..."
            )
            return await admin_main_menu(update, context)
        
    except ValueError as e:
        error_msg = str(e) if str(e) != "" else "Noto'g'ri format"
        await update.message.reply_text(
            f"❌ {error_msg}\n\n"
            "Iltimos, quyidagi formatda kiriting:\n\n"
            "Ism Familiya Telefon @username\n\n"
            "Misol: Anvar Xolmirzayev +998901234567 @anvar_xolmirzayev\n\n"
            "⚠️ Username @ belgisi bilan boshlanishi kerak!"
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
        if 'hodimlar' in storage.mahallalar[mahalla_nomi]:
            if lavozim in storage.mahallalar[mahalla_nomi]['hodimlar']:
                del storage.mahallalar[mahalla_nomi]['hodimlar'][lavozim]
        
        # Umumiy ro'yxatdan o'chirish
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
        for lavozim, username in hodimlar.items():
            if username in storage.staff_members:
                del storage.staff_members[username]
        
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
    
    keyboard = [
        [
            InlineKeyboardButton("✅ O'qildi", callback_data=f"read_{complaint['id']}"),
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

async def mark_complaint_read(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    complaint_id = int(query.data.replace("read_", ""))
    
    # Shikoyatni yangilash
    for complaint in storage.complaints:
        if complaint['id'] == complaint_id:
            complaint['status'] = 'o\'qildi'
            
            # Foydalanuvchiga xabar
            try:
                await context.bot.send_message(
                    chat_id=complaint['user_id'],
                    text=f"✅ #{complaint_id} raqamli shikoyatingiz o'qildi.\n"
                         f"Hodim masalani ko'rib chiqmoqda."
                )
            except:
                pass
            
            break
    
    storage.save_data()
    await query.edit_message_text("✅ Shikoyat o'qildi deb belgilandi.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Amal bekor qilindi.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def main():
    # Botni yaratish
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Foydalanuvchi uchun conversation handler
    user_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAHALLA_SELECT: [
                CallbackQueryHandler(select_mahalla, pattern='^mahalla_')
            ],
            PERSONAL_INFO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_personal_info)
            ],
            COMPLAINT_TEXT: [
                CallbackQueryHandler(select_staff, pattern='^staff_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_complaint_text)
            ],
            COMPLAINT_PHOTO: [
                CallbackQueryHandler(handle_complaint_photo, pattern='^(add_photo|no_photo)$'),
                MessageHandler(filters.PHOTO, handle_photo)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Admin uchun conversation handler
    admin_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_panel, pattern='^(add_mahalla|add_staff|remove_staff|delete_mahalla|add_group|stats)$')],
        states={
            ADMIN_MAIN: [
                CallbackQueryHandler(admin_panel, pattern='^(add_mahalla|add_staff|remove_staff|delete_mahalla|add_group|stats|admin_main)$')
            ],
            ADD_MAHALLA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_mahalla)
            ],
            SELECT_POSITION: [
                CallbackQueryHandler(select_position, pattern='^position_')
            ],
            ADD_STAFF: [
                CallbackQueryHandler(select_mahalla_for_staff, pattern='^staffmahalla_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_staff_member)
            ],
            REMOVE_STAFF: [
                CallbackQueryHandler(remove_staff_member, pattern='^removestaff_')
            ],
            DELETE_MAHALLA: [
                CallbackQueryHandler(delete_mahalla_confirm, pattern='^delmahalla_')
            ],
            ADD_GROUP: [
                CallbackQueryHandler(select_mahalla_for_group, pattern='^groupmahalla_'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_group_id)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Hodim uchun handler
    staff_handler = CallbackQueryHandler(staff_new_complaints, pattern='^new_complaints$')
    read_handler = CallbackQueryHandler(mark_complaint_read, pattern='^read_')
    
    # Handlerlarni qo'shish
    application.add_handler(user_conv_handler)
    application.add_handler(admin_conv_handler)
    application.add_handler(staff_handler)
    application.add_handler(read_handler)
    
    # Start handler
    application.add_handler(CommandHandler('start', start))
    
    # Botni ishga tushirish
    print("Bot ishga tushdi...")
    application.run_polling()

if __name__ == '__main__':
    main()
