from aiogram.fsm.state import State, StatesGroup


class NewLink(StatesGroup):
    files = State()


class AddChannel(StatesGroup):
    link = State()
    bind = State()
