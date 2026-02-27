# shop_bot.py
import telebot
from telebot import types
import sqlite3
import random
import string
import hashlib
import requests
import json
import uuid
from datetime import datetime
import time
import os
import hmac
import base64

# ===== НАСТРОЙКИ (ЗАМЕНИТЕ НА СВОИ) =====
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Токен от @BotFather
ADMIN_IDS = [8597342247]  # Ваш Telegram ID

# ЮKassa (https://yookassa.ru)
YOOKASSA_SHOP_ID = "your_shop_id"  # ID магазина
YOOKASSA_SECRET_KEY = "your_secret_key"  # Секретный ключ
YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"

# GitHub (для хранения ключей)
GITHUB_TOKEN = "your_github_token"  # Токен GitHub (Settings → Developer settings)
GITHUB_REPO = "yourusername/jarvis-keys"  # Например: "toni/jarvis-keys"
GITHUB_KEYS_PATH = "keys.json"  # Путь к файлу с ключами

# ===== БАЗА ДАННЫХ =====
def get_db():
    conn = sqlite3.connect('jarvis_shop.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Инициализация базы данных"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Пользователи
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Заказы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            user_id TEXT,
            amount INTEGER DEFAULT 300,
            status TEXT DEFAULT 'pending',
            payment_id TEXT,
            payment_method TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confirmed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Ключи
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            order_id TEXT UNIQUE,
            user_id TEXT,
            hwid TEXT,
            activations INTEGER DEFAULT 0,
            max_activations INTEGER DEFAULT 2,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            activated_at TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders(order_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def save_user(message):
    """Сохранение пользователя"""
    user_id = str(message.from_user.id)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
        (user_id, message.from_user.username, message.from_user.first_name)
    )
    conn.commit()
    conn.close()

def generate_order_id():
    """Генерация номера заказа"""
    timestamp = datetime.now().strftime("%y%m%d%H%M%S")
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"ORDER-{timestamp}-{random_part}"

def generate_license_key():
    """Генерация ключа"""
    def r():
        return ''.join(random.choices(string.digits, k=4))
    return f"JARVIS-{r()}-{r()}-{r()}"

def sync_keys_with_github():
    """Синхронизация ключей с GitHub"""
    try:
        # Получаем все ключи из БД
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT license_keys.*, orders.user_id 
            FROM license_keys 
            LEFT JOIN orders ON license_keys.order_id = orders.order_id
        ''')
        keys = cursor.fetchall()
        conn.close()
        
        # Формируем JSON для GitHub
        keys_data = {"keys": {}}
        for key in keys:
            keys_data["keys"][key['key']] = {
                "hwid": key['hwid'] or "",
                "activations": key['activations'],
                "max_activations": key['max_activations'],
                "user_id": key['user_id'],
                "created_at": key['created_at'],
                "activated_at": key['activated_at'] or ""
            }
        
        # Сохраняем локально
        with open('keys.json', 'w', encoding='utf-8') as f:
            json.dump(keys_data, f, ensure_ascii=False, indent=2)
        
        # Отправляем в GitHub (через API)
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_KEYS_PATH}"
        
        # Получаем текущий файл (чтобы получить sha)
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        get_response = requests.get(url, headers=headers)
        
        # Кодируем новый файл
        with open('keys.json', 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')
        
        # Данные для коммита
        data = {
            "message": f"Update keys {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "content": content,
            "branch": "main"
        }
        
        # Если файл уже существует, добавляем sha
        if get_response.status_code == 200:
            data["sha"] = get_response.json()['sha']
        
        # Отправляем
        put_response = requests.put(url, headers=headers, json=data)
        
        if put_response.status_code in [200, 201]:
            print(f"✅ Ключи синхронизированы с GitHub")
            return True
        else:
            print(f"❌ Ошибка GitHub: {put_response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка синхронизации: {e}")
        return False

def create_yookassa_payment(amount, description, order_id, user_id):
    """Создание платежа в ЮKassa"""
    idempotence_key = str(uuid.uuid4())
    
    payment_data = {
        "amount": {
            "value": f"{amount}.00",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{(bot.get_me()).username}?start={order_id}"
        },
        "capture": True,
        "description": description,
        "metadata": {
            "order_id": order_id,
            "user_id": user_id
        }
    }
    
    auth = (YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    headers = {
        "Idempotence-Key": idempotence_key,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            YOOKASSA_API_URL,
            json=payment_data,
            auth=auth,
            headers=headers,
            timeout=30
        )
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            print(f"Ошибка ЮKassa: {response.text}")
            return None
    except Exception as e:
        print(f"Ошибка запроса: {e}")
        return None

def check_payment_status(payment_id):
    """Проверка статуса платежа"""
    auth = (YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    
    try:
        response = requests.get(
            f"{YOOKASSA_API_URL}/{payment_id}",
            auth=auth,
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()
        return None
    except:
        return None

# ===== ОБРАБОТЧИКИ БОТА =====

@bot.message_handler(commands=['start'])
def start(message):
    save_user(message)
    
    # Проверяем, не перешли ли по ссылке с заказом
    args = message.text.split()
    if len(args) > 1:
        order_id = args[1]
        check_order_status(message.chat.id, order_id)
        return
    
    welcome = """
🤖 <b>Jarvis Shop</b>

Добро пожаловать! Здесь вы можете приобрести <b>Jarvis I</b> — голосовой помощник для компьютера.

💰 <b>Стоимость: 300₽</b> (разово, без подписок)

🎁 <b>Что входит:</b>
• Голосовое управление компьютером
• Управление через Telegram
• Создание своих команд
• 4 темы оформления
• Система обучения
• Плавающий реактор
• Все будущие обновления БЕСПЛАТНО

Выберите действие:
"""
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_buy = types.InlineKeyboardButton("💰 Купить за 300₽", callback_data="buy")
    btn_info = types.InlineKeyboardButton("ℹ️ Подробнее", callback_data="info")
    btn_key = types.InlineKeyboardButton("🔑 Проверить ключ", callback_data="check_key")
    btn_support = types.InlineKeyboardButton("📞 Поддержка", url="https://t.me/JarvisSupportBot")
    markup.add(btn_buy, btn_info)
    markup.add(btn_key, btn_support)
    
    bot.send_message(message.chat.id, welcome, parse_mode='HTML', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == "buy":
        process_purchase(call.message)
    elif call.data == "info":
        send_info(call.message)
    elif call.data == "check_key":
        ask_for_key(call.message)
    elif call.data.startswith("check_"):
        payment_id = call.data.replace("check_", "")
        check_payment(call.message, payment_id)

def process_purchase(message):
    """Начало процесса покупки"""
    user_id = str(message.chat.id)
    
    # Генерируем номер заказа
    order_id = generate_order_id()
    
    # Создаём платёж в ЮKassa
    payment = create_yookassa_payment(
        amount=300,
        description="Jarvis I - Голосовой помощник",
        order_id=order_id,
        user_id=user_id
    )
    
    if not payment:
        bot.send_message(
            message.chat.id,
            "❌ Временная ошибка. Попробуйте позже или напишите @JarvisSupportBot"
        )
        return
    
    # Сохраняем заказ в БД
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO orders (order_id, user_id, payment_id, status) VALUES (?, ?, ?, ?)",
        (order_id, user_id, payment['id'], 'pending')
    )
    conn.commit()
    conn.close()
    
    # Отправляем ссылку на оплату
    markup = types.InlineKeyboardMarkup()
    btn_pay = types.InlineKeyboardButton(
        "💳 Перейти к оплате", 
        url=payment['confirmation']['confirmation_url']
    )
    btn_check = types.InlineKeyboardButton(
        "✅ Я оплатил", 
        callback_data=f"check_{payment['id']}"
    )
    markup.add(btn_pay)
    markup.add(btn_check)
    
    msg = f"""
💳 <b>Оплата заказа #{order_id}</b>

Сумма: <b>300₽</b>

Способы оплаты:
• Карты РФ (МИР, Visa, MasterCard)
• СБП (Система быстрых платежей)
• ЮMoney
• Баланс телефона

👇 Нажмите кнопку для перехода на страницу оплаты
"""
    
    bot.send_message(
        message.chat.id,
        msg,
        parse_mode='HTML',
        reply_markup=markup
    )
    
    # Уведомление админу
    for admin_id in ADMIN_IDS:
        bot.send_message(
            admin_id,
            f"🆕 Новый заказ\n"
            f"Заказ: {order_id}\n"
            f"Пользователь: {user_id}\n"
            f"Платёж: {payment['id']}"
        )

def check_payment(message, payment_id):
    """Проверка статуса платежа"""
    msg = bot.send_message(message.chat.id, "⏳ Проверяю статус оплаты...")
    
    payment_info = check_payment_status(payment_id)
    
    if not payment_info:
        bot.edit_message_text(
            "❌ Ошибка проверки. Попробуйте через минуту.",
            message.chat.id,
            msg.message_id
        )
        return
    
    if payment_info['status'] == 'succeeded':
        # Платёж успешен
        conn = get_db()
        cursor = conn.cursor()
        
        # Находим заказ
        cursor.execute("SELECT * FROM orders WHERE payment_id = ?", (payment_id,))
        order = cursor.fetchone()
        
        if not order:
            bot.edit_message_text("❌ Заказ не найден", message.chat.id, msg.message_id)
            conn.close()
            return
        
        # Обновляем статус заказа
        cursor.execute(
            "UPDATE orders SET status = 'paid', confirmed_at = ? WHERE payment_id = ?",
            (datetime.now(), payment_id)
        )
        
        # Генерируем ключ
        key = generate_license_key()
        
        # Сохраняем ключ
        cursor.execute(
            "INSERT INTO license_keys (key, order_id, user_id) VALUES (?, ?, ?)",
            (key, order['order_id'], str(message.chat.id))
        )
        
        conn.commit()
        conn.close()
        
        # Синхронизируем с GitHub
        sync_keys_with_github()
        
        # Отправляем ключ пользователю
        success_msg = f"""
✅ <b>ОПЛАТА ПРОШЛА УСПЕШНО!</b>

🔑 <b>Ваш ключ активации:</b>
<code>{key}</code>

📥 <b>Скачать Jarvis I:</b>
https://your-server.com/download/Jarvis_I_Setup.exe

📌 <b>Инструкция по активации:</b>
1. Скачайте и установите программу
2. При первом запуске введите ключ
3. Ключ активируется на вашем компьютере
4. Можно активировать на 2 разных ПК

⚠️ <b>Сохраните ключ!</b> При переустановке системы он понадобится снова.

🎁 <b>Бонус:</b> все будущие обновления бесплатно!

Номер заказа: <code>{order['order_id']}</code>
"""
        
        bot.edit_message_text(
            success_msg,
            message.chat.id,
            msg.message_id,
            parse_mode='HTML'
        )
        
        # Уведомление админу
        for admin_id in ADMIN_IDS:
            bot.send_message(
                admin_id,
                f"💰 Продажа!\n"
                f"Заказ: {order['order_id']}\n"
                f"Пользователь: {message.chat.id}\n"
                f"Ключ: {key}"
            )
    
    elif payment_info['status'] == 'pending':
        bot.edit_message_text(
            "⏳ Платёж ещё не прошёл.\n"
            "Если вы уже оплатили, подождите несколько минут и нажмите 'Я оплатил' снова.",
            message.chat.id,
            msg.message_id
        )
    
    else:
        bot.edit_message_text(
            "❌ Платёж не найден или отменён.\n"
            "Попробуйте создать новый заказ /start",
            message.chat.id,
            msg.message_id
        )

def check_order_status(chat_id, order_id):
    """Проверка статуса заказа"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    order = cursor.fetchone()
    
    if not order:
        bot.send_message(chat_id, "❌ Заказ не найден")
        conn.close()
        return
    
    if order['status'] == 'paid':
        cursor.execute("SELECT * FROM license_keys WHERE order_id = ?", (order_id,))
        key_data = cursor.fetchone()
        
        if key_data:
            bot.send_message(
                chat_id,
                f"✅ Заказ оплачен!\nВаш ключ: <code>{key_data['key']}</code>",
                parse_mode='HTML'
            )
        else:
            bot.send_message(chat_id, "✅ Заказ оплачен, ключ скоро будет сгенерирован")
    else:
        bot.send_message(
            chat_id,
            f"⏳ Статус заказа: {order['status']}\n"
            f"Ссылка на оплату: {order['payment_id']}"
        )
    
    conn.close()

def send_info(message):
    """Информация о продукте"""
    info = """
<b>Jarvis I - Голосовой помощник</b>

🎤 <b>Голосовое управление:</b>
• Управляйте компьютером голосом
• Мультикоманды одной фразой
• Система обучается вашим привычкам

📱 <b>Удалённое управление:</b>
• Управляйте ПК через Telegram
• Делайте скриншоты
• Запускайте программы
• Выключайте компьютер

🎨 <b>Оформление:</b>
• 4 темы на выбор
• Плавающий реактор
• Красивая анимация

🎯 <b>Свои команды:</b>
• Создавайте свои голосовые команды
• Настраивайте под себя

💰 <b>Цена: 300₽</b> (разово, без подписок)
🎁 Все обновления бесплатно!
"""
    bot.send_message(message.chat.id, info, parse_mode='HTML')

def ask_for_key(message):
    """Запрос ключа для проверки"""
    msg = bot.send_message(message.chat.id, "Введите ваш ключ активации:")
    bot.register_next_step_handler(msg, check_key_status)

def check_key_status(message):
    """Проверка статуса ключа"""
    key = message.text.strip().upper()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM license_keys WHERE key = ?", (key,))
    key_data = cursor.fetchone()
    conn.close()
    
    if not key_data:
        bot.reply_to(message, "❌ Ключ не найден")
        return
    
    status = f"""
🔑 <b>Информация о ключе:</b>

Ключ: <code>{key_data['key']}</code>
Статус: {"✅ Активирован" if key_data['hwid'] else "⏳ Ожидает активации"}
Активаций: {key_data['activations']}/{key_data['max_activations']}
Создан: {key_data['created_at']}
"""
    
    if key_data['activated_at']:
        status += f"Активирован: {key_data['activated_at']}"
    
    bot.reply_to(message, status, parse_mode='HTML')

# ===== ЗАПУСК =====
if __name__ == '__main__':
    print("🤖 Jarvis Shop Bot запущен...")
    print(f"Админ ID: {ADMIN_IDS}")
    
    # Первая синхронизация с GitHub
    sync_keys_with_github()
    
    # Запуск бота
    bot.infinity_polling()