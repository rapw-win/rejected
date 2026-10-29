from aiogram.fsm.state import State, StatesGroup


class NewLink(StatesGroup):
    files = State()
    caption = State()


class EditCaption(StatesGroup):
    text = State()


class AddChannel(StatesGroup):
    link = State()
    bind = State()
