import os
import logging
from aiogram.types.message import ContentType
from aiogram.types.reply_keyboard import KeyboardButton, ReplyKeyboardMarkup
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.types.callback_query import CallbackQuery
from aiogram.utils import executor
from aiogram.types import (
    Message,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    ReplyKeyboardRemove
)

from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import filters

from api_handler import CoWin

load_dotenv()

API_SECRET = os.getenv("API_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)

storage = MemoryStorage()  # i'm goin with non persistent storage
dp = Dispatcher(bot, storage=storage)
cowin = CoWin()


class MyState(StatesGroup):
    init = State()
    phone = State()
    otp = State()
    txnId = State()
    token = State()
    benfs = State()


@dp.message_handler(commands="start")
async def start_message(message: Message):
    await message.answer(
        f"Hey {message.from_user.first_name}, I'll help you to generate covid vaccine certificate from cowin.gov.in right inside the telegram using cowin API, send /login to get started and you can use /cancel anytime for cancelling the current process.Feel free to checkout my source code at https://github.com/CW4RR10R/cowin-certificate-bot",
        disable_web_page_preview=True
    )


@dp.message_handler(state="*", commands="cancel")
@dp.message_handler(filters.Text(equals="cancel", ignore_case=True), state="*")
async def cancel_handler(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.finish()
    await message.answer("Cancelled, /login again", reply_markup=ReplyKeyboardRemove())


@dp.message_handler(commands="login")
async def login(message: Message):
    await MyState.init.set()
    btns = ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="choose any options",
    )
    btns.add(
        KeyboardButton("Login with your number", request_contact=True),
        KeyboardButton("Enter the number manually"),
    )
    btns.add(KeyboardButton("Cancel"))
    await message.answer(
        "I need to login your acccount inorder to fetch the vaccination info, so choose an input method for entering your phone number",
        reply_markup=btns
    )


@dp.message_handler(commands="logout", state="*")
async def logout(message: Message, state: FSMContext):
    if await state.get_state() is not None:
        await state.finish()
        await message.answer("successfully logged out")
    else:
        await message.answer("No session found")


@dp.message_handler(
    filters.Text(equals="Enter the number manually", ignore_case=True),
    state=MyState.init
)
async def gen_otp(message: Message, state: FSMContext):
    await message.answer(
        "Enter your phone number",
        reply_markup=ForceReply(input_field_placeholder="+91 (not required)")
    )
    await MyState.next()


@dp.message_handler(
    lambda message: not message.text.isdigit() or len(message.text) != 10,
    state=MyState.phone
)
async def handle_invalid_phone(message: Message):
    return await message.reply(
        "Invalid Phone number (number should have at least 10 digits)"
    )


@dp.message_handler(
    lambda message: (message.text.isdigit() and len(message.text) == 10),
    state=MyState.phone
)
@dp.message_handler(content_types=ContentType.CONTACT, state=MyState.init)
async def got_phone(message: Message, state: FSMContext):
    phone = message.text
    if contact := message.contact:
        phone = contact.phone_number[(len(contact.phone_number) - 10):]
        await MyState.next()
    await state.update_data(phone=phone)
    code, res = await cowin.generate_otp(
        phone,
        API_SECRET,
    )
    if code != 200:
        await message.reply(res["error"])
        return await state.finish()

    if txn_id := res.get("txnId"):
        await state.update_data(txnId=txn_id)
        await message.answer(
            f"An OTP has been sent to +91{phone}, enter that with in 3 minutes",
            reply_markup=ForceReply(input_field_placeholder="OTP should be integers"),
        )
        await MyState.next()


@dp.message_handler(lambda message: not message.text.isdigit(), state=MyState.otp)
async def handle_invalid_otp(message: Message):
    return await message.reply("Invalid OTP, enter again")


@dp.message_handler(state=MyState.otp)
async def got_otp(message: Message, state: FSMContext):
    otp = message.text
    await MyState.next()
    await state.update_data(otp=otp)
    async with state.proxy() as data:
        code, res = await cowin.confirm_otp(otp, data["txnId"])
        if code != 200:
            await message.reply(res["error"])
            return await state.finish()

        msg = await message.answer("OTP verified")
        if token := res.get("token"):
            await state.update_data(token=token)
            code, res_ben = await cowin.list_beneficiaries(token)
            if code != 200:
                await msg.edit(res_ben["error"])
                return await state.finish()
            if benfs := res_ben.get("beneficiaries"):
                await state.update_data(benfs=benfs)
                buttons = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                f"{ben['name']} - {ben['beneficiary_reference_id'][-4:]}",
                                callback_data=f"ben-{ben['beneficiary_reference_id']}",
                            )
                        ]
                        for ben in benfs
                    ]
                )
                buttons.add(InlineKeyboardButton("Logout", callback_data="logout"))
                await msg.edit_text(
                    f"{len(benfs)} beneficiaries found, select any of them",
                    reply_markup=buttons,
                )
            else:
                await msg.edit_text(
                    "No beneficiaries found, login with another number or register via https://selfregistration.cowin.gov.in"
                )
                return await state.finish()
        else:
            await msg.edit_text("couldnt find token, try again later")
            return await state.finish()


@dp.callback_query_handler(regexp=r"^ben\-\d+$", state="*")
async def select_ben(cb: CallbackQuery, state: FSMContext):
    ben_id = cb.data.split("-")[1]
    if await state.get_state() is None:
        return await cb.answer("sesssion expired, please login again", True)
    async with state.proxy() as data:
        sben = next(
            item for item in data["benfs"] if item["beneficiary_reference_id"] == ben_id
        )
        await cb.message.edit_text(
            f"Name: {sben['name']}\nYOB: {sben['birth_year']}\n"
            f"Gender: {sben['gender']}\nVaccination status: {sben['vaccination_status']}\n"
            f"Vaccine: {sben['vaccine']}\nDose 1 Date: {sben['dose1_date']}\nDose 2 Date: {sben['dose2_date']}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            "Download certificate", callback_data=f"cert-{ben_id}"
                        )
                    ],
                    [InlineKeyboardButton("Back", callback_data="back_to_ben_list")],
                ]
            ),
        )
    await cb.answer()


@dp.callback_query_handler(regexp=r"^cert\-\d+$", state="*")
async def get_certificate(cb: CallbackQuery, state: FSMContext):
    ben_id = cb.data.split("-")[1]
    if await state.get_state() is None:
        return await cb.answer("sesssion expired, please login again", True)
    temp_text = cb.message.text
    temp_btn = cb.message.reply_markup
    await cb.answer()
    async with state.proxy() as data:
        await cb.message.edit_text("Trying to download the certificate...")
        certificate_path = await cowin.download_certificate(data["token"], ben_id)
        if certificate_path is None:
            await cb.message.edit_text("failed to find certificate login again")
            return await state.finish()
        await cb.message.edit_text("Uploading your certificate....")
        await cb.message.answer_document(InputFile(certificate_path))
        await cb.message.edit_text(temp_text, reply_markup=temp_btn)
        os.remove(certificate_path)


@dp.callback_query_handler(regexp=r"^back_to_ben_list$", state="*")
async def back_to_ben(cb: CallbackQuery, state: FSMContext):
    if await state.get_state() is None:
        return await cb.answer("sesssion expired, please login again", True)
    async with state.proxy() as data:
        buttons = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        f"{ben['name']} - {ben['beneficiary_reference_id'][-4:]}",
                        callback_data=f"ben-{ben['beneficiary_reference_id']}",
                    )
                ]
                for ben in data["benfs"]
            ]
        )
        buttons.add(InlineKeyboardButton("Logout", callback_data="logout"))
        await cb.message.edit_text(
            f"{len(data['benfs'])} beneficiaries found, select any of them",
            reply_markup=buttons,
        )
        await cb.answer()


@dp.callback_query_handler(regexp=r"^logout$", state="*")
async def logout(cb: Message, state: FSMContext):
    if await state.get_state() is None:
        return await cb.answer("sesssion expired, please login again", True)
    await cb.message.edit_text("successfully logged out")
    await state.finish()
    await cb.answer()


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
