from telegram import Update, KeyboardButton, WebAppInfo, ReplyKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ApplicationBuilder,
)
from nordigen import NordigenClient
from dotenv import load_dotenv
from datetime import datetime
from functools import wraps
from loguru import logger
from uuid import uuid4
import database as db
import requests
import os


load_dotenv()


class BankBot:
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

    async def require_exchange(self, response, update, context, function):
        if response.status_code == 401:
            logger.warning("Tokens are expired, getting new tokens...")
            self.init_token = self.client.exchange_token(self.init_token["refresh"])
            await function(update, context)
        elif response.status_code in [400, 403, 404, 405, 408, 429, 500, 502, 503, 504]:
            logger.error(
                f"User {update.message.from_user.id} tried to make a request in {function.__name__} but request was unsuccessful.\nRequest failed with code {response.status_code} and message {response.text}"
            )

    @log_info
    async def on_start(self, update: Update, callback: CallbackContext) -> None:
        user_id = update.message.from_user.id

        if db.user_exists(user_id):
            if db.is_authorized(user_id):
                await self.authenticated(update, callback)
                return

            login_keyboard = [
                [
                    KeyboardButton(
                        "Login",
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
                    "Authenticate Bank",
                    web_app=WebAppInfo(db.get_auth_link(user_id)),
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
                    "Get Transactions",
                ),
                KeyboardButton(
                    "Get Balance",
                ),
            ]
        ]

        main_keyboard.extend([[KeyboardButton("Settings")]])

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

        await self.require_exchange(response, update, context, self.get_balance)

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

    @log_info
    async def get_transactions(self, update: Update, context: CallbackContext) -> None:
        logger.info(
            f"User {update.message.from_user.id} pressed Get Transactions at {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        await update.message.reply_text("♻️ Getting transactions...")

        url = f"https://bankaccountdata.gocardless.com/api/v2/accounts/{db.get_account_id(update.message.from_user.id)}/transactions/"

        response = requests.get(
            url,
            headers={
                "accept": "application/json",
                "Authorization": f"Bearer {self.init_token['access']}",
            },
        )

        await self.require_exchange(response, update, context, self.get_transactions)

        messages_list = []
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

                messages_list.append(data_message)

        messages_list.reverse()

        for final_message in messages_list:
            await update.message.reply_text(final_message, parse_mode="MarkdownV2")

    @log_info
    async def settings(self, update: Update, context: CallbackContext) -> None:
        pass

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
            MessageHandler(filters.Text("Login"), self.bank_init)
        )

        self.application.add_handler(
            MessageHandler(filters.Text("Get Balance"), self.get_balance)
        )
        self.application.add_handler(
            MessageHandler(filters.Text("Get Transactions"), self.get_transactions)
        )
        self.application.add_handler(
            MessageHandler(filters.Text("Settings"), self.settings)
        )

        self.application.add_handler(
            MessageHandler(filters.StatusUpdate.WEB_APP_DATA, self.authenticated)
        )

        logger.success(
            f"Bot initialized successfully at {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )

        self.application.run_polling()


if __name__ == "__main__":
    bot = BankBot(os.getenv("BOT_TOKEN"))
    bot.run_bot()
