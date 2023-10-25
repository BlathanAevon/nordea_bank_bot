import sqlite3 as sq
import json

db = sq.connect("bank_users.db")
cur = db.cursor()


def db_init():
    cur.execute(
        "CREATE TABLE IF NOT EXISTS bank_users ("
        "telegram_id INTEGER PRIMARY KEY, "
        "auth_link TEXT, "
        "requisition_id TEXT, "
        "bank_account_id TEXT, "
        "is_authorized INTEGER DEFAULT 0, "
        "tx_notify INTEGER DEFAULT 0, "
        "last_tx TEXT"
        ")"
    )

    db.commit()


def insert_user(
    telegram_id,
    auth_link=None,
    requisition_id=None,
    bank_account_id=None,
    is_authorized=False,
    tx_notify=False,
    last_tx=None,
):
    try:
        cur.execute(
            "INSERT INTO bank_users (telegram_id, auth_link, requisition_id, bank_account_id, is_authorized, tx_notify, last_tx) "
            "VALUES (?, ?, ?, ?, ?, ?, ?,)",
            (
                telegram_id,
                auth_link,
                requisition_id,
                bank_account_id,
                is_authorized,
                tx_notify,
                last_tx,
            ),
        )
        db.commit()
        print("User inserted successfully.")
    except sq.Error as e:
        print("Error inserting user:", e)


def user_exists(telegram_id):
    try:
        cur.execute(
            "SELECT COUNT(*) FROM bank_users WHERE telegram_id = ?", (telegram_id,)
        )
        count = cur.fetchone()[0]
        return count > 0  # Return True if the user exists, False otherwise
    except sq.Error as e:
        print("Error checking user existence:", e)
        return False  # Return False on error


def is_authorized(telegram_id):
    try:
        cur.execute(
            "SELECT is_authorized FROM bank_users WHERE telegram_id = ?", (telegram_id,)
        )
        result = cur.fetchone()
        if result is not None:
            return bool(
                result[0]
            )  # Return True if the user is authorized, False otherwise
        else:
            return False  # User not found, return False
    except sq.Error as e:
        print("Error checking authorization:", e)
        return False  # Return False on error


def check_user_authorization(telegram_id):
    if user_exists(telegram_id):
        return (
            True,
            is_authorized(telegram_id),
        )  # Return a tuple (True, True) or (True, False) based on existence and authorization
    else:
        return (False, False)


def get_auth_link(telegram_id):
    try:
        cur.execute(
            "SELECT auth_link FROM bank_users WHERE telegram_id = ?", (telegram_id,)
        )
        result = cur.fetchone()
        if result is not None:
            return result[0]  # Return the auth_link if the user is found
        else:
            return None  # User not found, return None
    except sq.Error as e:
        print("Error retrieving auth link:", e)
        return None  # Return None on error


def get_requisition_id(telegram_id):
    try:
        cur.execute(
            "SELECT requisition_id FROM bank_users WHERE telegram_id = ?",
            (telegram_id,),
        )
        result = cur.fetchone()
        if result is not None:
            return result[0]  # Return the requisition_id if the user is found
        else:
            return None  # User not found, return None
    except sq.Error as e:
        print("Error retrieving requisition ID:", e)
        return None  # Return None on error


def get_account_id(telegram_id):
    try:
        cur.execute(
            "SELECT bank_account_id FROM bank_users WHERE telegram_id = ?",
            (telegram_id,),
        )
        result = cur.fetchone()
        if result is not None:
            return result[0]  # Return the bank_account_id if the user is found
        else:
            return None  # User not found, return None
    except sq.Error as e:
        print("Error retrieving bank account ID:", e)
        return None  # Return None on error


def insert_auth_link(telegram_id, auth_link):
    try:
        cur.execute(
            "UPDATE bank_users SET auth_link = ? WHERE telegram_id = ?",
            (auth_link, telegram_id),
        )
        db.commit()
        print(f"Auth Link updated for user {telegram_id}")
    except sq.Error as e:
        print("Error updating auth link:", e)


def insert_requisition_id(telegram_id, requisition_id):
    try:
        cur.execute(
            "UPDATE bank_users SET requisition_id = ? WHERE telegram_id = ?",
            (requisition_id, telegram_id),
        )
        db.commit()
        print(f"Requisition ID updated for user {telegram_id}")
    except sq.Error as e:
        print("Error updating requisition ID:", e)


def insert_account_id(telegram_id, bank_account_id):
    try:
        cur.execute(
            "UPDATE bank_users SET bank_account_id = ? WHERE telegram_id = ?",
            (bank_account_id, telegram_id),
        )
        db.commit()
        print(f"Bank Account ID updated for user {telegram_id}")
    except sq.Error as e:
        print("Error updating bank account ID:", e)


def insert_is_authorized(telegram_id, is_authorized):
    try:
        cur.execute(
            "UPDATE bank_users SET is_authorized = ? WHERE telegram_id = ?",
            (is_authorized, telegram_id),
        )
        db.commit()
        print(f"is_authorized updated for user {telegram_id} to {is_authorized}")
    except sq.Error as e:
        print("Error updating is_authorized:", e)


def get_tx_notify(telegram_id):
    try:
        # Select tx_notify for the specified telegram_id
        cur.execute(
            "SELECT tx_notify FROM bank_users WHERE telegram_id = ?", (telegram_id,)
        )
        result = cur.fetchone()

        if result:
            return result
        else:
            print(f"User with telegram_id {telegram_id} not found.")
            return None

    except sq.Error as e:
        print("Error getting tx_notify:", e)
        return None


def set_tx_notify(telegram_id: int, value: bool) -> None:
    try:
        # Update the tx_notify field for the specified telegram_id
        cur.execute(
            "UPDATE bank_users SET tx_notify = ? WHERE telegram_id = ?",
            (value, telegram_id),
        )
        db.commit()
        print(f"tx_notify updated for user {telegram_id} to {value}")

    except sq.Error as e:
        print("Error updating tx_notify:", e)


def get_telegram_ids() -> list:
    ids_list = []
    try:
        # Select all users' telegram_id values
        cur.execute("SELECT telegram_id FROM bank_users")
        telegram_ids = cur.fetchall()

        for telegram_id in telegram_ids:
            ids_list.append(telegram_id[0])

        return ids_list

    except sq.Error as e:
        print("Error fetching telegram_ids:", e)


def get_users_to_notify():
    try:
        # Select all users' telegram_id and tx_notify
        cur.execute("SELECT telegram_id, tx_notify FROM bank_users")
        user_records = cur.fetchall()

        user_info_dict = {}
        for record in user_records:
            telegram_id, tx_notify = record
            user_info_dict[telegram_id] = {"tx_notify": bool(tx_notify)}

        return user_info_dict

    except sq.Error as e:
        print("Error fetching user info:", e)
        return {}


def get_last_tx(telegram_id):
    try:
        # Select the last_tx for the specified telegram_id
        cur.execute(
            "SELECT last_tx FROM bank_users WHERE telegram_id = ?", (telegram_id,)
        )
        result = cur.fetchone()

        if result:
            (last_tx,) = result
            return result
        else:
            print(f"User with telegram_id {telegram_id} not found.")
            return None

    except sq.Error as e:
        print("Error getting last_tx:", e)
        return None


import sqlite3 as sq
import json


def set_last_tx(telegram_id, last_tx):
    try:
        # Update the last_tx for the specified telegram_id
        cur.execute(
            "UPDATE bank_users SET last_tx = ? WHERE telegram_id = ?",
            (last_tx, telegram_id),
        )
        db.commit()

        print(f"Last transaction set for user with telegram_id {telegram_id}")
    except sq.Error as e:
        print("Error setting last_tx:", e)


# Usage example
# Replace 'telegram_id' and 'last_tx_data' with your actual values
telegram_id = "12345"
last_tx_data = {"transaction_id": 1, "amount": 100.0, "description": "Payment"}
set_last_tx(telegram_id, last_tx_data)
