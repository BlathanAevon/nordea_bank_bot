from telegram import Update, KeyboardButton, WebAppInfo, ReplyKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ApplicationBuilder,
    CallbackQueryHandler,
    ConversationHandler,
)
from nordigen import NordigenClient
from dotenv import load_dotenv
from datetime import datetime
from functools import wraps
from random import randint
from loguru import logger
from uuid import uuid4
import database as db
import requests
import os


load_dotenv()


class BankBot:
    AWAITING_MESSAGE = 0

    def __init__(self, bot_token):
        self.bot_token = bot_token
        self.application = None
        self.client = None
        self.init_token = None

    @staticmethod
    def log_info(func):
        @wraps(func)
        async def wrapper(
            self, update: Update, context: CallbackContext, *args, **kwargs
        ):
            logger.info(
                f"User {update.message.from_user.id} wrote {func.__name__} at {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            )
            result = await func(self, update, context, *args, **kwargs)
            return result

        return wrapper

    async def refresh_token(self) -> None:
        logger.warning("Tokens are expired, getting new tokens...")
        self.init_token = self.client.generate_token()

    @log_info
    async def on_start(self, update: Update, callback: CallbackContext) -> None:
        user_id = update.message.from_user.id

        if db.user_exists(user_id):
            if db.is_authorized(user_id):
                try:
                    await self.authenticated(update, callback)
                    if db.get_tx_notify(user_id)[0]:
                        self.remove_job_if_exists(f"tx_checker_{user_id}", callback)
                        callback.job_queue.run_repeating(
                            self.new_tx_trigger,
                            randint(60, 120),
                            chat_id=user_id,
                            name=f"tx_checker_{user_id}",
                            data=callback,
                        )
                    return
                except requests.HTTPError:
                    await self.refresh_token()
                    await self.authenticated(update, callback)
                    return

            login_keyboard = [
                [
                    KeyboardButton(
                        "💠 Login",
                    )
                ]
            ]

            await update.message.reply_text(
                f"👨 Hello {update.message.from_user.first_name}! You are not authorized in your bank, please do that to continue using bot",
                reply_markup=ReplyKeyboardMarkup(login_keyboard, resize_keyboard=True),
            )

            return

        else:
            db.insert_user(user_id)

            await update.message.reply_text(
                f"👨 Hello {update.message.from_user.first_name}! Welcome to Nordea Bank Checker, please authenticate in your bank."
            )
            try:
                await self.bank_init(update, callback)
            except requests.HTTPError:
                await self.refresh_token()
                await self.bank_init(update, callback)
            return

    @log_info
    async def bank_init(self, update: Update, context: CallbackContext) -> None:
        logger.info("INITIALIZING A SESSION")

        user_id = update.message.from_user.id

        init = self.client.initialize_session(
            institution_id=self.institution_id,
            redirect_uri=os.getenv("WEB_APP_URL"),
            reference_id=str(uuid4()),
        )

        auth_link = init.link
        requisition_id = init.requisition_id

        db.insert_auth_link(user_id, auth_link)
        db.insert_requisition_id(user_id, requisition_id)

        keyboard = [
            [
                KeyboardButton(
                    "👨‍💻 Authenticate Bank",
                    web_app=WebAppInfo(auth_link),
                )
            ]
        ]

        await update.message.reply_text(
            "🧭 Bank session created, please authenticate",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        )

    @log_info
    async def authenticated(self, update: Update, context: CallbackContext) -> None:
        user_id = update.message.from_user.id

        account_id = self.client.requisition.get_requisition_by_id(
            requisition_id=db.get_requisition_id(user_id)
        )["accounts"][0]

        db.insert_account_id(user_id, account_id)

        if not db.is_authorized(user_id):
            db.insert_is_authorized(user_id, 1)

        await update.message.reply_text("✅ Authentication Successful! ✅")
        await update.message.reply_text("♻️ Getting Account details...")

        account = self.client.account_api(id=db.get_account_id(user_id))

        account_details = account.get_details()

        await update.message.reply_text(
            f"✅ Account Connected! ✅\nWelcome!\n\n🙎‍♂️ Account Owner: {account_details['account']['ownerName']}\n💳Account Name: {account_details['account']['product']} "
        )

        main_keyboard = [
            [
                KeyboardButton(
                    "📇 Get Transactions",
                ),
                KeyboardButton(
                    "💳 Get Balance",
                ),
            ]
        ]

        if str(user_id) == os.getenv("ADMIN_ID"):
            main_keyboard.append(
                [
                    KeyboardButton("🔊 Notify Everyone"),
                    KeyboardButton("⚙️ Settings"),
                ]
            )
        else:
            main_keyboard.extend([[KeyboardButton("⚙️ Settings")]])

        await update.message.reply_text(
            "🏫 Choose an option:",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True),
        )

    @log_info
    async def get_balance(self, update: Update, context: CallbackContext) -> None:
        await update.message.reply_text("♻️ Getting balance...")

        url = f"https://bankaccountdata.gocardless.com/api/v2/accounts/{db.get_account_id(update.message.from_user.id)}/balances/"

        response = requests.get(
            url,
            headers={
                "accept": "application/json",
                "Authorization": f"Bearer {self.init_token['access']}",
            },
        )
        if response.status_code == 401:
            logger.error(
                f"User {update.message.from_user.id} tried to make a request but request was unsuccessful.\nRequest failed with code {response.status_code} and message {response.text}"
            )
            await self.refresh_token()
            response = requests.get(
                url,
                headers={
                    "accept": "application/json",
                    "Authorization": f"Bearer {self.init_token['access']}",
                },
            )

        balance = next(
            (
                balance["balanceAmount"]["amount"]
                for balance in response.json().get("balances", [])
                if balance.get("balanceType") == "interimAvailable"
            ),
            None,
        )

        await update.message.reply_text(
            f"💸 Account Balance is {balance} SEK",
        )

    async def get_transactions_logic(self, user_id) -> str:
        url = f"https://bankaccountdata.gocardless.com/api/v2/accounts/{db.get_account_id(user_id)}/transactions/"

        response = requests.get(
            url,
            headers={
                "accept": "application/json",
                "Authorization": f"Bearer {self.init_token['access']}",
            },
        )

        if response.status_code == 401:
            logger.error(
                f"User {user_id} tried to make a request but request was unsuccessful.\nRequest failed with code {response.status_code} and message {response.text}"
            )
            await self.refresh_token()
            response = requests.get(
                url,
                headers={
                    "accept": "application/json",
                    "Authorization": f"Bearer {self.init_token['access']}",
                },
            )

        return response

    def format_transactons(self, response: str) -> list:
        booked_list = []
        pending_list = []

        for transaction_type in ["pending", "booked"]:
            for transaction in response.json()["transactions"].get(
                transaction_type, []
            )[:11]:
                transaction_summ = float(transaction["transactionAmount"]["amount"])
                transaction_amount = f"{transaction_summ} SEK"
                transaction_type = transaction[
                    "remittanceInformationUnstructured"
                ].strip("*")

                if transaction_summ < 0:
                    inverted_summ = transaction_summ * -1
                    transaction_amount = f"**{inverted_summ}** SEK"

                if "Överföring" in transaction_type:
                    if transaction_summ < 0:
                        transaction_type = (
                            f"🔄 #Transfer  to {transaction_type.strip('Överföring')}"
                        )
                    else:
                        transaction_type = (
                            f"🔄 #Transfer  from {transaction_type.strip('Överföring')}"
                        )
                elif "Kortköp" in transaction_type:
                    transaction_type = f"💳 #CardPayment  to {transaction_type.strip('Kortköp')[7:]}".replace(
                        "*", ""
                    )
                elif "Lön" in transaction_type:
                    transaction_type = "💰 #MonthlySalary"
                elif "" in transaction_type:
                    transaction_type = (
                        f"🏦 #ServicePayment  to {transaction_type.strip('Betalning')}"
                    )

                transaction_date = datetime.strptime(
                    transaction["transactionId"], "%Y-%m-%d-%H.%M.%S.%f"
                ).strftime("%d.%m.%Y ⌛ %H:%M")
                data_message = f"{transaction_type}\n\n💵 Amount: {transaction_amount}\n\n🗓️ Date: {transaction_date}"

                characters_to_escape = [".", "-", "(", ")", "#"]
                data_message = "".join(
                    [
                        "\\" + char if char in characters_to_escape else char
                        for char in data_message
                    ]
                )

                if transaction_type == "booked":
                    booked_list.append(data_message)
                else:
                    pending_list.append(data_message)

        messages_list = booked_list + pending_list

        return messages_list

    @log_info
    async def get_transactions(self, update: Update, context: CallbackContext) -> None:
        logger.info(
            f"User {update.message.from_user.id} pressed Get Transactions at {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        await update.message.reply_text("♻️ Getting transactions...")

        response = await self.get_transactions_logic(update.message.from_user.id)

        messages_list = self.format_transactons(response)

        messages_list.reverse()

        last_tx = messages_list[-1]
        db.set_last_tx(update.message.from_user.id, last_tx)

        for final_message in messages_list:
            await update.message.reply_text(final_message, parse_mode="MarkdownV2")

    async def notification_keyboard(
        self, update: Update, context: CallbackContext
    ) -> None:
        if db.get_tx_notify(update.message.from_user.id)[0]:
            settings_keyboard = [
                [
                    KeyboardButton(
                        "⬅️ Back",
                    ),
                    KeyboardButton(
                        "❌ Disable Notificatons",
                    ),
                ]
            ]

            await update.message.reply_text(
                "🏫 Choose an option:",
                reply_markup=ReplyKeyboardMarkup(
                    settings_keyboard, resize_keyboard=True
                ),
            )
        else:
            settings_keyboard = [
                [
                    KeyboardButton(
                        "⬅️ Back",
                    ),
                    KeyboardButton(
                        "✅ Enable Notifications",
                    ),
                ]
            ]

            await update.message.reply_text(
                "🏫 Choose an option:",
                reply_markup=ReplyKeyboardMarkup(
                    settings_keyboard, resize_keyboard=True
                ),
            )

    @log_info
    async def settings(self, update: Update, context: CallbackContext) -> None:
        await self.notification_keyboard(update, context)

    @log_info
    async def back_button_handler(self, update: Update, context: CallbackContext):
        main_keyboard = [
            [
                KeyboardButton(
                    "📇 Get Transactions",
                ),
                KeyboardButton(
                    "💳 Get Balance",
                ),
            ]
        ]

        if str(update.message.from_user.id) == os.getenv("ADMIN_ID"):
            main_keyboard.append(
                [
                    KeyboardButton("🔊 Notify Everyone"),
                    KeyboardButton("⚙️ Settings"),
                ]
            )
        else:
            main_keyboard.extend([[KeyboardButton("⚙️ Settings")]])

        await update.message.reply_text(
            "🏫 Choose an option:",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True),
        )

    @log_info
    async def notify_everyone(self, update: Update, context: CallbackContext):
        await update.message.reply_text("🗣 Enter notification:")
        return self.AWAITING_MESSAGE

    @log_info
    async def handle_notification(
        self, update: Update, context: CallbackContext
    ) -> int:
        for telegram_id in db.get_telegram_ids():
            if db.is_authorized(telegram_id):
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=f"⚠️ NOTIFICATION FOR ALL USERS!⚠️\n\n{update.message.text}",
                )

        await update.message.reply_text("🟢 Notifications successfully sent!")

        return ConversationHandler.END

    async def new_tx_trigger(self, context: CallbackContext) -> None:
        job = context.job
        chat_id = job.chat_id

        logger.info(f"DOING JOB FOR {chat_id}")

        response = await self.get_transactions_logic(chat_id)
        current_last_tx = self.format_transactons(response)
        current_last_tx.reverse()
        current_last_tx = current_last_tx[-1]

        last_tx = db.get_last_tx(chat_id)

        max_retries = 3

        if last_tx[0] != current_last_tx:
            db.set_last_tx(chat_id, current_last_tx)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"💸 NEW TRANSACTION 💸\n\n{current_last_tx}",
                parse_mode="MarkdownV2",
            )
        else:
            for _ in range(max_retries):
                response = await self.get_transactions_logic(chat_id)
                current_last_tx = self.format_transactons(response)
                current_last_tx.reverse()
                current_last_tx = current_last_tx[-1]

                if last_tx[0] != current_last_tx:
                    db.set_last_tx(chat_id, current_last_tx)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"💸 NEW TRANSACTION 💸\n\n{current_last_tx}",
                        parse_mode="MarkdownV2",
                    )
                    break

    def remove_job_if_exists(self, name: str, context: CallbackContext) -> bool:
        """Remove job with given name. Returns whether job was removed."""
        current_jobs = context.job_queue.get_jobs_by_name(name)
        if not current_jobs:
            return False
        for job in current_jobs:
            job.schedule_removal()
        return True

    async def enable_notificatons(
        self, update: Update, context: CallbackContext
    ) -> None:
        user_id = update.message.from_user.id

        db.set_tx_notify(user_id, True)

        context.job_queue.run_repeating(
            self.new_tx_trigger,
            randint(60, 120),
            chat_id=user_id,
            name=f"tx_checker_{user_id}",
            data=context,
        )

        response = await self.get_transactions_logic(user_id)
        messages_list = self.format_transactons(response)
        messages_list.reverse()

        last_tx = db.get_last_tx(user_id)
        current_last_tx = messages_list[-1:]

        if last_tx[0] != current_last_tx[0]:
            db.set_last_tx(user_id, current_last_tx[0])

        await update.message.reply_text("🔈 Transactions notifications enabled.")
        await self.notification_keyboard(update, context)

    async def disable_notifications(
        self, update: Update, context: CallbackContext
    ) -> None:
        user_id = update.message.from_user.id

        job_removed = self.remove_job_if_exists(f"tx_checker_{user_id}", context)

        if job_removed:
            db.set_tx_notify(update.message.from_user.id, False)
            await update.message.reply_text("🔇 Transactions notifications disabled.")
            await self.notification_keyboard(update, context)
        else:
            await update.message.reply_text(
                "📟 Transactions notifications are not changed."
            )

    def run_bot(self) -> None:
        db.db_init()
        logger.success(
            f"Database initialized at {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

        self.client = NordigenClient(
            secret_id=os.getenv("SECRET_ID"), secret_key=os.getenv("SECRET_KEY")
        )
        logger.success(f"Client created at {datetime.now().strftime('%d.%m.%Y %H:%M')}")

        self.init_token = self.client.generate_token()

        self.institution_id = self.client.institution.get_institution_id_by_name(
            country="SE", institution="Nordea Personal"
        )

        logger.success(
            f"Bank data received at {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

        self.application = ApplicationBuilder().token(self.bot_token).build()
        self.application.add_handler(CommandHandler("start", self.on_start))

        self.application.add_handler(
            MessageHandler(filters.Text("💠 Login"), self.bank_init)
        )

        self.application.add_handler(
            MessageHandler(filters.Text("💳 Get Balance"), self.get_balance)
        )
        self.application.add_handler(
            MessageHandler(filters.Text("📇 Get Transactions"), self.get_transactions)
        )
        self.application.add_handler(
            MessageHandler(filters.Text("⬅️ Back"), self.back_button_handler)
        )

        self.application.add_handler(
            MessageHandler(filters.Text("⚙️ Settings"), self.settings)
        )
        self.application.add_handler(
            MessageHandler(
                filters.Text("❌ Disable Notificatons"), self.disable_notifications
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.Text("✅ Enable Notifications"), self.enable_notificatons
            )
        )

        self.application.add_handler(
            MessageHandler(filters.StatusUpdate.WEB_APP_DATA, self.authenticated)
        )

        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Text("🔊 Notify Everyone"), self.notify_everyone)
            ],
            states={
                self.AWAITING_MESSAGE: [
                    MessageHandler(filters.Text(), self.handle_notification)
                ],
            },
            fallbacks=[],
        )

        self.application.add_handler(conv_handler)
        self.application.add_handler(
            CallbackQueryHandler(
                self.handle_notification, pattern="🗣 Enter notification:"
            )
        )

        logger.success(
            f"Bot initialized successfully at {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

        self.application.run_polling()


if __name__ == "__main__":
    bot = BankBot(os.getenv("BOT_TOKEN"))
    bot.run_bot()
