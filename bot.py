import telebot
from telebot import types
from tinydb import TinyDB, Query
import requests
from datetime import datetime, timedelta
import uuid
import threading
import time

from config import (
    TOKEN, ADMIN_ID, CRYPTOBOT_TOKEN, CRYPTOBOT_API_URL, DB_CHANNEL_ID,
    TON_WALLET, TON_CHECK_TIMEOUT, TON_API_URL
)

bot = telebot.TeleBot(TOKEN)
db = TinyDB('database.json')
users = db.table('users')
products = db.table('products')
stats = db.table('stats')
categories = db.table('categories')
ton_payments = db.table('ton_payments')

# ---------- TON helpers ----------
def get_ton_transactions():
    url = TON_API_URL.format(TON_WALLET)
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json().get("transactions", [])
    except Exception as e:
        print("TON API err:", e)
    return []

def find_payment_by_comment(comment, amount):
    for tx in get_ton_transactions():
        try:
            msg = tx.get("in_msg", {})
            if msg.get("message") == comment:
                value = int(msg.get("value", 0)) / 1e9   # nanoton → ton
                if value >= amount * 0.999:
                    return value
        except Exception:
            continue
    return None

def ton_payment_timer(user_id, comment, amount, chat_id, msg_id):
    start = time.time()
    while time.time() - start < TON_CHECK_TIMEOUT:
        if find_payment_by_comment(comment, amount):
            user = users.get(Query().user_id == user_id)
            users.update({
                'balance': user['balance'] + amount,
                'total_deposited': user['total_deposited'] + amount
            }, Query().user_id == user_id)
            stats.insert({'type': 'payment', 'amount': amount,
                          'timestamp': datetime.now().strftime('%Y-%m-%d')})
            bot.edit_message_text("✅ TON платёж найден! Баланс пополнен.", chat_id, msg_id)
            return
        time.sleep(30)
    bot.edit_message_text("❌ Платёж не поступил. Заявка отменена.", chat_id, msg_id)

def ton_get_amount(message):
    try:
        amount = float(message.text)
        if amount < 0.1:
            raise ValueError("Минимум 0.1 TON")
        comment = str(uuid.uuid4())[:8]
        user_id = message.from_user.id
        ton_payments.insert({
            'user_id': user_id,
            'amount': amount,
            'comment': comment,
            'status': 'pending',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        text = (
            f"➕ Пополнение TON ➕\n\n"
            f"💰 Сумма: <b>{amount}</b> TON\n"
            f"📨 Комментарий: <code>{comment}</code>\n"
            f"💎 Кошелёк: <code>{TON_WALLET}</code>\n\n"
            f"Отправьте точную сумму на кошелёк с указанным комментарием.\n"
            f"⚠️ Без комментария платёж не зачтётся.\n"
            f"⏳ Время на оплату: 30 минут"
        )
        sent = bot.send_message(message.chat.id, text, parse_mode='HTML')
        threading.Thread(target=ton_payment_timer,
                         args=(user_id, comment, amount, message.chat.id, sent.message_id),
                         daemon=True).start()
    except ValueError as e:
        bot.send_message(message.chat.id, str(e))

# ---------- старый код без изменений ----------
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    if not users.get(Query().user_id == user_id):
        users.insert({
            'user_id': user_id,
            'balance': 0,
            'purchases': 0,
            'total_spent': 0,
            'total_deposited': 0,
            'join_date': datetime.now().strftime('%Y-%m-%d')
        })
        stats.insert({'type': 'new_user', 'timestamp': datetime.now().strftime('%Y-%m-%d')})
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("🏪 Купить"), types.KeyboardButton("📋 Товары"))
    markup.row(types.KeyboardButton("👤 Профиль"), types.KeyboardButton("💳 Пополнить баланс"))
    bot.send_message(message.chat.id, "<b>Привет! Добро пожаловать в наш магазин!</b>", reply_markup=markup, parse_mode='HTML')

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.send_message(message.chat.id, "У вас нет доступа к админ-панели")
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("Добавить товар", callback_data="admin_add"),
        types.InlineKeyboardButton("Удалить товар", callback_data="admin_delete")
    )
    markup.add(
        types.InlineKeyboardButton("Создать раздел", callback_data="admin_create_category"),
        types.InlineKeyboardButton("Удалить раздел", callback_data="admin_delete_category")
    )
    markup.add(types.InlineKeyboardButton("Статистика", callback_data="admin_stats"))
    bot.send_message(message.chat.id, "Админ меню", reply_markup=markup)

@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    if message.text == "👤 Профиль":
        user = users.get(Query().user_id == user_id)
        if user:
            text = (
                "Ваш профиль:\n"
                f" Ваш ID: <code>{user_id}</code>\n\n"
                "Информация:\n"
                f"├ Сумма покупок: <code>{user['total_spent']}$</code>\n"
                f"└ Сумма пополнений: <code>{user['total_deposited']}$</code>\n\n"
                f"🏦 Ваш баланс: <code>{user['balance']}$</code>"
            )
            bot.send_message(chat_id, text, parse_mode='HTML')
        else:
            bot.send_message(chat_id, "Профиль не найден. Попробуйте перезапустить бота с помощью /start")
    elif message.text == "💳 Пополнить баланс":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🤖 USDT (CryptoBot)", callback_data="pay_usdt"))
        markup.add(types.InlineKeyboardButton("💎 TON (на кошелёк)", callback_data="pay_ton"))
        bot.send_message(chat_id, "➖ Пополнение баланса ➖\n\nВыберите способ:", reply_markup=markup)
    elif message.text == "🏪 Купить":
        markup = types.InlineKeyboardMarkup()
        all_categories = categories.all()
        for i in range(0, len(all_categories), 2):
            row = []
            row.append(types.InlineKeyboardButton(all_categories[i]['name'], callback_data=f"category_{all_categories[i]['id']}"))
            if i + 1 < len(all_categories):
                row.append(types.InlineKeyboardButton(all_categories[i+1]['name'], callback_data=f"category_{all_categories[i+1]['id']}"))
            markup.add(*row)
        bot.send_message(chat_id, "Каталог", reply_markup=markup)
    elif message.text == "📋 Товары":
        show_products_list(chat_id, 1)

def process_amount(message):
    try:
        amount = float(message.text)
        if not (1 <= amount <= 1500):
            raise ValueError("Сумма должна быть от 1$ до 1500$")
        markup = types.InlineKeyboardMarkup()
        invoice = create_cryptobot_invoice(amount, message.from_user.id)
        markup.add(types.InlineKeyboardButton("🌍 Перейти к оплате", url=invoice['pay_url']))
        markup.add(types.InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_{invoice['invoice_id']}"))
        bot.send_message(message.chat.id, f"➖ Пополнение ➖\n\n💰 Сумма: <code>{amount}$</code>", reply_markup=markup, parse_mode='HTML')
    except ValueError as e:
        bot.send_message(message.chat.id, str(e))

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    if call.data == "pay_ton":
        bot.delete_message(chat_id, message_id)
        msg = bot.send_message(chat_id, "➖ Пополнение TON ➖\n\nВведите сумму (мин. 0.1 TON):")
        bot.register_next_step_handler(msg, ton_get_amount)
        return
    if call.data == "pay_usdt":
        bot.delete_message(chat_id, message_id)
        msg = bot.send_message(chat_id, "➖ Пополнение баланса ➖\n\nВведите сумму пополнения, от 1$ до 1500$:")
        bot.register_next_step_handler(msg, process_amount)
        return
    if call.data.startswith("check_"):
        invoice_id = call.data.split("_")[1]
        status = check_payment(invoice_id)
        if status:
            amount = float(status['amount'])
            user = users.get(Query().user_id == call.from_user.id)
            users.update({
                'balance': user['balance'] + amount,
                'total_deposited': user['total_deposited'] + amount
            }, Query().user_id == call.from_user.id)
            stats.insert({'type': 'payment', 'amount': amount, 'timestamp': datetime.now().strftime('%Y-%m-%d')})
            bot.edit_message_text("✅ Оплата успешно завершена!", chat_id, message_id)
        else:
            bot.answer_callback_query(call.id, "Платеж еще не завершен")
        return
    # ---------- остальные callbackи без изменений ----------
    if call.data.startswith("category_"):
        category_id = int(call.data.split("_")[1])
        category = categories.get(Query().id == category_id)
        if category:
            bot.delete_message(chat_id, message_id)
            markup = types.InlineKeyboardMarkup()
            items = products.search(Query().category_id == category_id)
            for i in range(0, len(items), 2):
                row = []
                row.append(types.InlineKeyboardButton(items[i]['name'], callback_data=f"item_{items[i]['id']}"))
                if i + 1 < len(items):
                    row.append(types.InlineKeyboardButton(items[i+1]['name'], callback_data=f"item_{items[i+1]['id']}"))
                markup.add(*row)
            markup.add(types.InlineKeyboardButton("Назад", callback_data="back_to_catalog"))
            bot.send_message(chat_id, f"Каталог: {category['name']}", reply_markup=markup)
        return
    if call.data.startswith("item_"):
        item_id = int(call.data.split("_")[1])
        item = products.get(Query().id == item_id)
        if item:
            bot.delete_message(chat_id, message_id)
            text = (
                "➖ Покупка ➖\n\n"
                f"📦 Товар: {item['name']}\n"
                f"💰 Цена: {item['price']}$\n\n"
                "Описание:\n"
                f"{item['description']}"
            )
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("🛍 Купить", callback_data=f"buy_{item_id}"),
                types.InlineKeyboardButton("Назад", callback_data=f"category_{item['category_id']}"))
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')
        return
    if call.data == "back_to_catalog":
        bot.delete_message(chat_id, message_id)
        markup = types.InlineKeyboardMarkup()
        all_categories = categories.all()
        for i in range(0, len(all_categories), 2):
            row = []
            row.append(types.InlineKeyboardButton(all_categories[i]['name'], callback_data=f"category_{all_categories[i]['id']}"))
            if i + 1 < len(all_categories):
                row.append(types.InlineKeyboardButton(all_categories[i+1]['name'], callback_data=f"category_{all_categories[i+1]['id']}"))
            markup.add(*row)
        bot.send_message(chat_id, "Каталог", reply_markup=markup)
        return
    if call.data.startswith("buy_"):
        item_id = int(call.data.split("_")[1])
        item = products.get(Query().id == item_id)
        if item:
            bot.delete_message(chat_id, message_id)
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("Да", callback_data=f"confirm_{item_id}"),
                types.InlineKeyboardButton("Отмена", callback_data=f"item_{item_id}")
            )
            bot.send_message(chat_id, "<b>Вы точно хотите купить этот товар?</b>", reply_markup=markup, parse_mode='HTML')
        return
    if call.data.startswith("confirm_"):
        item_id = int(call.data.split("_")[1])
        item = products.get(Query().id == item_id)
        user_id = call.from_user.id
        user = users.get(Query().user_id == user_id)
        if item and user:
            if user['balance'] >= item['price']:
                bot.delete_message(chat_id, message_id)
                users.update({
                    'balance': user['balance'] - item['price'],
                    'purchases': user['purchases'] + 1,
                    'total_spent': user['total_spent'] + item['price']
                }, Query().user_id == user_id)
                bot.send_message(chat_id, "⚡️")
                if 'file_id' in item:
                    bot.send_document(chat_id, item['file_id'], caption=f"Ваш товар: {item['name']}")
            else:
                bot.edit_message_text("❌ Недостаточно средств на балансе!", chat_id, message_id)
        return
    if call.data == "admin_stats":
        stats_text = get_stats()
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Назад", callback_data="admin_back"))
        bot.edit_message_text(stats_text, chat_id, message_id, reply_markup=markup, parse_mode='HTML')
        return
    if call.data == "admin_back":
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("Добавить товар", callback_data="admin_add"),
            types.InlineKeyboardButton("Удалить товар", callback_data="admin_delete")
        )
        markup.add(
            types.InlineKeyboardButton("Создать раздел", callback_data="admin_create_category"),
            types.InlineKeyboardButton("Удалить раздел", callback_data="admin_delete_category")
        )
        markup.add(types.InlineKeyboardButton("Статистика", callback_data="admin_stats"))
        bot.edit_message_text("Админ меню", chat_id, message_id, reply_markup=markup)
        return
    if call.data == "admin_add":
        markup = types.InlineKeyboardMarkup()
        all_categories = categories.all()
        for i in range(0, len(all_categories), 2):
            row = []
            row.append(types.InlineKeyboardButton(all_categories[i]['name'], callback_data=f"admin_select_category_{all_categories[i]['id']}"))
            if i + 1 < len(all_categories):
                row.append(types.InlineKeyboardButton(all_categories[i+1]['name'], callback_data=f"admin_select_category_{all_categories[i+1]['id']}"))
            markup.add(*row)
        bot.send_message(chat_id, "Выберите раздел для создания товара", reply_markup=markup)
        return
    if call.data == "admin_create_category":
        bot.delete_message(chat_id, message_id)
        msg = bot.send_message(chat_id, "Введите название раздела")
        bot.register_next_step_handler(msg, create_category)
        return
    if call.data == "admin_delete_category":
        show_categories_to_delete(chat_id)
        return
    if call.data == "admin_delete":
        show_products_to_delete(chat_id)
        return
    if call.data.startswith("admin_select_category_"):
        category_id = int(call.data.split("_")[3])
        bot.delete_message(chat_id, message_id)
        msg = bot.send_message(chat_id, "Введите название товара")
        bot.register_next_step_handler(msg, lambda m: add_product_name(m, category_id))
        return
    if call.data.startswith("products_page_"):
        page = int(call.data.split("_")[2])
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=update_products_list(chat_id, page), reply_markup=update_pagination(page), parse_mode='HTML')

# ---------- остальные функции без изменений ----------
def create_category(message):
    if not message.text:
        bot.send_message(message.chat.id, "Название раздела не может быть пустым!")
        return
    name = message.text
    category_id = len(categories) + 1
    categories.insert({'id': category_id, 'name': name})
    bot.send_message(message.chat.id, "Раздел создан")

def show_categories_to_delete(chat_id):
    all_categories = categories.all()
    if not all_categories:
        bot.send_message(chat_id, "Разделов пока нет")
        return
    text = "<b>Список разделов</b>\n\n"
    for i, category in enumerate(all_categories, 1):
        text += f"{i}. <b>{category['name']}</b>\n"
    text += "\n<b>Введите номер раздела, который хотите удалить</b>"
    msg = bot.send_message(chat_id, text, parse_mode='HTML')
    bot.register_next_step_handler(msg, delete_category)

def delete_category(message):
    try:
        num = int(message.text) - 1
        all_categories = categories.all()
        if 0 <= num < len(all_categories):
            category_id = all_categories[num]['id']
            products.remove(Query().category_id == category_id)
            categories.remove(doc_ids=[all_categories[num].doc_id])
            bot.send_message(message.chat.id, "Раздел удален!")
        else:
            bot.send_message(message.chat.id, "Неверный номер раздела!")
    except ValueError:
        bot.send_message(message.chat.id, "Введите корректный номер!")

def create_cryptobot_invoice(amount, user_id):
    headers = {
        'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN,
        'Content-Type': 'application/json'
    }
    payload = {
        'amount': str(amount),
        'asset': 'USDT',
        'description': f'Пополнение баланса пользователя {user_id}'
    }
    try:
        response = requests.post(f'{CRYPTOBOT_API_URL}createInvoice', headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if 'ok' in data and data['ok'] and 'result' in data:
            return data['result']
        else:
            error_msg = data.get('error', 'Неизвестная ошибка')
            raise Exception(f"Ошибка API Crypto Bot: {error_msg}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ошибка соединения с Crypto Bot: {str(e)}")
    except ValueError as e:
        raise Exception(f"Ошибка обработки ответа от Crypto Bot: {str(e)}")

def check_payment(invoice_id):
    headers = {
        'Crypto-Pay-API-Token': CRYPTOBOT_TOKEN,
        'Content-Type': 'application/json'
    }
    try:
        response = requests.get(f'{CRYPTOBOT_API_URL}getInvoices?invoice_ids={invoice_id}', headers=headers)
        response.raise_for_status()
        data = response.json()
        if data['ok'] and 'result' in data and data['result']['items']:
            return data['result']['items'][0] if data['result']['items'][0]['status'] == 'paid' else False
        return False
    except Exception as e:
        return False

def get_stats():
    Stat = Query()
    today = datetime.now().strftime('%Y-%m-%d')
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    users_day = len(stats.search((Stat.type == 'new_user') & (Stat.timestamp >= today)))
    users_week = len(stats.search((Stat.type == 'new_user') & (Stat.timestamp >= week_ago)))
    users_total = len(stats.search(Stat.type == 'new_user'))
    payments_day = len(stats.search((Stat.type == 'payment') & (Stat.timestamp >= today)))
    payments_week = len(stats.search((Stat.type == 'payment') & (Stat.timestamp >= week_ago)))
    payments_total = len(stats.search(Stat.type == 'payment'))
    return (
        "<b>📊 Статистика:</b>\n\n"
        "<b>👤 Юзеры:</b>\n"
        f"За день: <code>{users_day}</code>\n"
        f"За неделю: <code>{users_week}</code>\n"
        f"За Всё время: <code>{users_total}</code>\n\n"
        "<b>💰Пополнения:</b>\n"
        f"Пополнений за День: <code>{payments_day}</code>\n"
        f"Пополнений за Неделю: <code>{payments_week}</code>\n"
        f"Пополнений за Все время: <code>{payments_total}</code>"
    )

def add_product_name(message, category_id):
    if not message.text:
        bot.send_message(message.chat.id, "Название не может быть пустым!")
        return
    name = message.text
    bot.delete_message(message.chat.id, message.message_id - 1)
    msg = bot.send_message(message.chat.id, "Введите описание товара")
    bot.register_next_step_handler(msg, lambda m: add_product_desc(m, name, category_id))

def add_product_desc(message, name, category_id):
    if not message.text:
        bot.send_message(message.chat.id, "Описание не может быть пустым!")
        return
    desc = message.text
    bot.delete_message(message.chat.id, message.message_id - 1)
    msg = bot.send_message(message.chat.id, "Введите цену товара")
    bot.register_next_step_handler(msg, lambda m: add_product_price(m, name, desc, category_id))

def add_product_price(message, name, desc, category_id):
    try:
        price = float(message.text)
        if price <= 0:
            raise ValueError("Цена должна быть положительной")
        bot.delete_message(message.chat.id, message.message_id - 1)
        msg = bot.send_message(message.chat.id, "Отправьте файл который будет отправляться после покупки")
        bot.register_next_step_handler(msg, lambda m: add_product_file(m, name, desc, price, category_id))
    except ValueError:
        bot.send_message(message.chat.id, "Введите корректную положительную цену!")

def add_product_file(message, name, desc, price, category_id):
    if message.content_type == 'document':
        file_msg = bot.send_document(DB_CHANNEL_ID, message.document.file_id)
        file_id = file_msg.document.file_id
        bot.delete_message(message.chat.id, message.message_id - 1)
        product_id = len(products) + 1
        products.insert({
            'id': product_id,
            'name': name,
            'description': desc,
            'price': price,
            'file_id': file_id,
            'category_id': category_id
        })
        bot.send_message(message.chat.id, "Товар создан")
    else:
        bot.send_message(message.chat.id, "Пожалуйста, отправьте документ!")

def update_products_list(chat_id, page):
    items = products.all()
    total_items = len(items)
    items_per_page = 32
    total_pages = (total_items + items_per_page - 1) // items_per_page
    if total_pages == 0:
        return "Товаров пока нет"
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    start_idx = (page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, total_items)
    text = ""
    for item in items[start_idx:end_idx]:
        text += f"<b>{item['name']}</b> | <code>{item['price']}$</code>\n"
    return text

def update_pagination(page):
    items = products.all()
    total_items = len(items)
    items_per_page = 32
    total_pages = (total_items + items_per_page - 1) // items_per_page
    if total_pages <= 1:
        return None
    markup = types.InlineKeyboardMarkup()
    row = []
    if page == 1 and total_pages > 1:
        row.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        row.append(types.InlineKeyboardButton("Вперед ▶️", callback_data=f"products_page_{page+1}"))
    elif page == total_pages and total_pages > 1:
        row.append(types.InlineKeyboardButton("Назад ◀️", callback_data=f"products_page_{page-1}"))
        row.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    else:
        row.append(types.InlineKeyboardButton("Назад ◀️", callback_data=f"products_page_{page-1}"))
        row.append(types.InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        row.append(types.InlineKeyboardButton("Вперед ▶️", callback_data=f"products_page_{page+1}"))
    markup.add(*row)
    return markup

def show_products_list(chat_id, page):
    text = update_products_list(chat_id, page)
    markup = update_pagination(page)
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')

def show_products_to_delete(chat_id):
    items = products.all()
    if not items:
        bot.send_message(chat_id, "Товаров пока нет")
        return
    text = "<b>Список товаров</b>\n\n"
    for i, item in enumerate(items, 1):
        text += f"{i}. <b>{item['name']}</b> | <code>{item['price']}$</code>\n"
    text += "\n<b>Введите номер товара который хотите удалить</b>"
    msg = bot.send_message(chat_id, text, parse_mode='HTML')
    bot.register_next_step_handler(msg, delete_product)

def delete_product(message):
    try:
        num = int(message.text) - 1
        items = products.all()
        if 0 <= num < len(items):
            products.remove(doc_ids=[items[num].doc_id])
            bot.send_message(message.chat.id, "Товар удален!")
        else:
            bot.send_message(message.chat.id, "Неверный номер товара!")
    except ValueError:
        bot.send_message(message.chat.id, "Введите корректный номер!")

if __name__ == "__main__":
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"Ошибка при запуске бота: {str(e)}")
