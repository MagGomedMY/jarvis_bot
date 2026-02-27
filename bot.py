import logging
import asyncio
from datetime import datetime
import os
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, select, update

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен бота из .env файла
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_IDS = [int(id) for id in os.getenv('ADMIN_IDS', '').split(',') if id]

# Состояния для ConversationHandler
DESCRIPTION, SCREENSHOT, CONFIRM = range(3)

# Настройка базы данных
DATABASE_URL = "sqlite+aiosqlite:///jarvis_support.db"
engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# Модель для хранения ошибок
class ErrorReport(Base):
    __tablename__ = 'error_reports'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    username = Column(String(255))
    description = Column(Text, nullable=False)
    screenshot_id = Column(String(255))
    status = Column(String(50), default='new')  # new, in_progress, resolved, closed
    priority = Column(String(20), default='medium')  # low, medium, high, critical
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_by = Column(Integer)
    resolution_notes = Column(Text)

# Модель для пользователей
class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    is_blocked = Column(Boolean, default=False)
    reports_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)

async def init_db():
    """Инициализация базы данных"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Сохраняем или обновляем пользователя
    await save_or_update_user(user)
    
    welcome_text = (
        f"👋 Здравствуйте, {user.first_name}!\n\n"
        "Я бот поддержки голосового помощника Jarvis. "
        "Если вы столкнулись с какой-либо ошибкой или проблемой в работе помощника, "
        "вы можете сообщить об этом здесь.\n\n"
        "Доступные команды:\n"
        "/report - Сообщить об ошибке\n"
        "/my_reports - Мои обращения\n"
        "/status - Проверить статус обращения\n"
        "/help - Помощь"
    )
    
    await update.message.reply_text(welcome_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📚 Справка по использованию бота:\n\n"
        "1. Чтобы сообщить об ошибке, используйте команду /report\n"
        "2. Опишите проблему максимально подробно\n"
        "3. При необходимости прикрепите скриншот\n"
        "4. Наши специалисты рассмотрят обращение в ближайшее время\n\n"
        "Для проверки статуса обращения используйте /status"
    )
    await update.message.reply_text(help_text)

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса сообщения об ошибке"""
    await update.message.reply_text(
        "Пожалуйста, опишите проблему подробно:\n"
        "• Что именно произошло?\n"
        "• В какой момент возникла ошибка?\n"
        "• Какие действия вы предпринимали?\n\n"
        "Отправьте /cancel для отмены."
    )
    return DESCRIPTION

async def report_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение описания ошибки"""
    context.user_data['description'] = update.message.text
    
    keyboard = [
        [InlineKeyboardButton("Да, добавить скриншот", callback_data='add_screenshot')],
        [InlineKeyboardButton("Нет, отправить без скриншота", callback_data='no_screenshot')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Хотите добавить скриншот ошибки?",
        reply_markup=reply_markup
    )
    return SCREENSHOT

async def report_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка скриншота"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'add_screenshot':
        await query.edit_message_text(
            "Пожалуйста, отправьте скриншот ошибки.\n"
            "Это поможет нам быстрее разобраться в проблеме."
        )
        return SCREENSHOT
    else:
        # Пропускаем добавление скриншота
        return await confirm_report(query, context)

async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка полученного скриншота"""
    if update.message.photo:
        # Получаем самое большое изображение
        photo = update.message.photo[-1]
        context.user_data['screenshot_id'] = photo.file_id
        
        return await confirm_report(update, context)
    else:
        await update.message.reply_text(
            "Пожалуйста, отправьте изображение или нажмите /skip, чтобы пропустить этот шаг."
        )
        return SCREENSHOT

async def confirm_report(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение отправки отчета"""
    description = context.user_data.get('description', '')
    has_screenshot = 'screenshot_id' in context.user_data
    
    confirm_text = (
        f"📝 Проверьте данные:\n\n"
        f"Описание: {description[:200]}{'...' if len(description) > 200 else ''}\n"
        f"Скриншот: {'✅ Добавлен' if has_screenshot else '❌ Не добавлен'}\n\n"
        "Всё верно?"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Отправить", callback_data='submit')],
        [InlineKeyboardButton("✏️ Редактировать", callback_data='edit')],
        [InlineKeyboardButton("❌ Отмена", callback_data='cancel')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Проверяем тип объекта (Update или CallbackQuery)
    if hasattr(update_or_query, 'message'):
        await update_or_query.message.reply_text(confirm_text, reply_markup=reply_markup)
    else:
        await update_or_query.edit_message_text(confirm_text, reply_markup=reply_markup)
    
    return CONFIRM

async def submit_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка отчета в базу данных"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    description = context.user_data.get('description')
    screenshot_id = context.user_data.get('screenshot_id')
    
    # Сохраняем отчет в базу данных
    report_id = await save_error_report(user, description, screenshot_id)
    
    # Обновляем статистику пользователя
    await increment_user_reports(user.id)
    
    await query.edit_message_text(
        f"✅ Спасибо! Ваше обращение #{report_id} успешно отправлено.\n\n"
        f"Наши специалисты рассмотрят его в ближайшее время.\n"
        f"Вы можете проверить статус обращения командой /status"
    )
    
    # Уведомляем администраторов
    await notify_admins(context.bot, user, report_id, description)
    
    # Очищаем данные пользователя
    context.user_data.clear()
    
    return ConversationHandler.END

async def edit_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактирование отчета"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Начнем заново. Опишите проблему:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data='cancel')
        ]])
    )
    return DESCRIPTION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена действия"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("❌ Действие отменено.")
    else:
        await update.message.reply_text("❌ Действие отменено.")
    
    context.user_data.clear()
    return ConversationHandler.END

async def my_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр своих обращений"""
    user_id = update.effective_user.id
    
    reports = await get_user_reports(user_id)
    
    if not reports:
        await update.message.reply_text("У вас пока нет обращений.")
        return
    
    text = "📋 Ваши обращения:\n\n"
    for report in reports[:5]:  # Показываем последние 5
        status_emoji = {
            'new': '🆕',
            'in_progress': '🔄',
            'resolved': '✅',
            'closed': '🔒'
        }.get(report.status, '❓')
        
        text += (
            f"{status_emoji} #{report.id} - {report.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"Статус: {report.status}\n"
            f"Описание: {report.description[:100]}...\n\n"
        )
    
    await update.message.reply_text(text)

async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса конкретного обращения"""
    args = context.args
    
    if not args:
        await update.message.reply_text(
            "Укажите номер обращения:\n"
            "/status 123"
        )
        return
    
    try:
        report_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Пожалуйста, укажите корректный номер обращения.")
        return
    
    report = await get_report_by_id(report_id, update.effective_user.id)
    
    if not report:
        await update.message.reply_text("Обращение не найдено или не принадлежит вам.")
        return
    
    status_text = (
        f"📊 Статус обращения #{report.id}\n\n"
        f"📅 Создано: {report.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📝 Описание: {report.description}\n"
        f"📌 Статус: {report.status}\n"
        f"⚡ Приоритет: {report.priority}\n"
    )
    
    if report.resolution_notes:
        status_text += f"\n💬 Комментарий: {report.resolution_notes}"
    
    await update.message.reply_text(status_text)

# Функции для работы с БД
async def save_or_update_user(telegram_user):
    """Сохранение или обновление пользователя"""
    async with AsyncSessionLocal() as session:
        # Проверяем существование пользователя
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_user.id)
        )
        user = result.scalar_one_or_none()
        
        if user:
            user.username = telegram_user.username
            user.first_name = telegram_user.first_name
            user.last_name = telegram_user.last_name
            user.last_active = datetime.utcnow()
        else:
            user = User(
                telegram_id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name
            )
            session.add(user)
        
        await session.commit()

async def save_error_report(telegram_user, description, screenshot_id):
    """Сохранение отчета об ошибке"""
    async with AsyncSessionLocal() as session:
        report = ErrorReport(
            user_id=telegram_user.id,
            username=telegram_user.username,
            description=description,
            screenshot_id=screenshot_id,
            status='new',
            priority='medium'
        )
        session.add(report)
        await session.commit()
        return report.id

async def increment_user_reports(telegram_id):
    """Увеличение счетчика обращений пользователя"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(reports_count=User.reports_count + 1)
        )
        await session.commit()

async def get_user_reports(telegram_id):
    """Получение обращений пользователя"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ErrorReport)
            .where(ErrorReport.user_id == telegram_id)
            .order_by(ErrorReport.created_at.desc())
        )
        return result.scalars().all()

async def get_report_by_id(report_id, user_id):
    """Получение отчета по ID"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ErrorReport)
            .where(ErrorReport.id == report_id)
            .where(ErrorReport.user_id == user_id)
        )
        return result.scalar_one_or_none()

# Админские функции
async def notify_admins(bot, user, report_id, description):
    """Уведомление администраторов о новом обращении"""
    for admin_id in ADMIN_IDS:
        try:
            text = (
                f"🔔 Новое обращение #{report_id}\n\n"
                f"👤 От: {user.full_name} (@{user.username})\n"
                f"📝 Описание: {description[:200]}{'...' if len(description) > 200 else ''}\n"
                f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
            
            keyboard = [[
                InlineKeyboardButton(
                    "📋 Посмотреть обращение", 
                    callback_data=f"view_report_{report_id}"
                )
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {admin_id}: {e}")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Панель администратора"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для доступа к этой команде.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data='admin_stats')],
        [InlineKeyboardButton("📋 Новые обращения", callback_data='admin_new_reports')],
        [InlineKeyboardButton("🔄 В работе", callback_data='admin_in_progress')],
        [InlineKeyboardButton("✅ Решенные", callback_data='admin_resolved')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👨‍💼 Панель администратора\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик админских callback запросов"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("У вас нет прав для этого действия.")
        return
    
    data = query.data
    
    if data == 'admin_stats':
        await show_admin_stats(query)
    elif data.startswith('view_report_'):
        report_id = int(data.split('_')[2])
        await show_report_details(query, report_id)
    elif data.startswith('change_status_'):
        parts = data.split('_')
        report_id = int(parts[2])
        new_status = parts[3]
        await change_report_status(query, report_id, new_status)
    elif data.startswith('set_priority_'):
        parts = data.split('_')
        report_id = int(parts[2])
        priority = parts[3]
        await set_report_priority(query, report_id, priority)

async def show_admin_stats(query):
    """Показывает статистику для админа"""
    async with AsyncSessionLocal() as session:
        # Общая статистика
        total = await session.execute(select(ErrorReport))
        total_count = len(total.scalars().all())
        
        new = await session.execute(
            select(ErrorReport).where(ErrorReport.status == 'new')
        )
        new_count = len(new.scalars().all())
        
        in_progress = await session.execute(
            select(ErrorReport).where(ErrorReport.status == 'in_progress')
        )
        in_progress_count = len(in_progress.scalars().all())
        
        resolved = await session.execute(
            select(ErrorReport).where(ErrorReport.status == 'resolved')
        )
        resolved_count = len(resolved.scalars().all())
        
        # Статистика пользователей
        users = await session.execute(select(User))
        users_count = len(users.scalars().all())
        
        stats_text = (
            "📊 Статистика обращений:\n\n"
            f"Всего обращений: {total_count}\n"
            f"🆕 Новых: {new_count}\n"
            f"🔄 В работе: {in_progress_count}\n"
            f"✅ Решенных: {resolved_count}\n\n"
            f"👥 Всего пользователей: {users_count}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='admin_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup)

async def show_report_details(query, report_id):
    """Показывает детали обращения"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ErrorReport).where(ErrorReport.id == report_id)
        )
        report = result.scalar_one_or_none()
        
        if not report:
            await query.edit_message_text("Обращение не найдено.")
            return
        
        # Получаем информацию о пользователе
        user_result = await session.execute(
            select(User).where(User.telegram_id == report.user_id)
        )
        user = user_result.scalar_one_or_none()
        
        report_text = (
            f"📋 Обращение #{report.id}\n\n"
            f"👤 Пользователь: {user.first_name if user else 'Неизвестно'} "
            f"(@{report.username or 'нет username'})\n"
            f"📅 Создано: {report.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"📝 Описание: {report.description}\n"
            f"📌 Статус: {report.status}\n"
            f"⚡ Приоритет: {report.priority}\n"
        )
        
        if report.screenshot_id:
            report_text += "\n📸 Скриншот прикреплен"
        
        # Кнопки управления
        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 В работу", 
                    callback_data=f"change_status_{report_id}_in_progress"
                ),
                InlineKeyboardButton(
                    "✅ Решено", 
                    callback_data=f"change_status_{report_id}_resolved"
                )
            ],
            [
                InlineKeyboardButton(
                    "⚡ Приоритет", 
                    callback_data=f"set_priority_{report_id}_high"
                )
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data='admin_back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(report_text, reply_markup=reply_markup)
        
        # Отправляем скриншот если есть
        if report.screenshot_id:
            await query.message.reply_photo(
                report.screenshot_id,
                caption=f"Скриншот к обращению #{report.id}"
            )

async def change_report_status(query, report_id, new_status):
    """Изменяет статус обращения"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ErrorReport)
            .where(ErrorReport.id == report_id)
            .values(
                status=new_status,
                resolved_by=query.from_user.id,
                updated_at=datetime.utcnow()
            )
        )
        await session.commit()
    
    await query.edit_message_text(
        f"✅ Статус обращения #{report_id} изменен на '{new_status}'"
    )

async def set_report_priority(query, report_id, priority):
    """Устанавливает приоритет обращения"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ErrorReport)
            .where(ErrorReport.id == report_id)
            .values(priority=priority, updated_at=datetime.utcnow())
        )
        await session.commit()
    
    await query.edit_message_text(
        f"✅ Приоритет обращения #{report_id} изменен на '{priority}'"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "Произошла внутренняя ошибка. Наши специалисты уже работают над её устранением."
            )
    except:
        pass

async def main():
    """Главная функция"""
    # Инициализация БД
    await init_db()
    
    # Создание приложения
    application = Application.builder().token(TOKEN).build()
    
    # Создание ConversationHandler для отчета об ошибке
    report_conv = ConversationHandler(
        entry_points=[CommandHandler('report', report_start)],
        states={
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_description)],
            SCREENSHOT: [
                CallbackQueryHandler(report_screenshot, pattern='^(add_screenshot|no_screenshot)$'),
                MessageHandler(filters.PHOTO, handle_screenshot),
                CommandHandler('skip', lambda u, c: confirm_report(u, c))
            ],
            CONFIRM: [CallbackQueryHandler(submit_report, pattern='^submit$'),
                     CallbackQueryHandler(edit_report, pattern='^edit$'),
                     CallbackQueryHandler(cancel, pattern='^cancel$')]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        name="report_conversation"
    )
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(report_conv)
    application.add_handler(CommandHandler('my_reports', my_reports))
    application.add_handler(CommandHandler('status', check_status))
    application.add_handler(CommandHandler('admin', admin_panel))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern='^(admin_|view_report_|change_status_|set_priority_)'))
    
    # Глобальный обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запуск бота
    print("🤖 Бот поддержки Jarvis запущен...")
    print(f"📱 Авторизован как @{(await application.bot.get_me()).username}")
    print("⏳ Ожидание сообщений...")
    
    # Запуск polling
    await application.run_polling(allowed_updates=Update.ALL_TYPE)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())