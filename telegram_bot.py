"""Telegram chat with one trained LoRA adapter. Run on a machine with CUDA."""
from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path

from common import config, generate, load_model

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)


class TokenRedactingFormatter(logging.Formatter):
    def __init__(self, token):
        super().__init__("%(asctime)s %(levelname)s %(message)s")
        self.token = token

    def format(self, record):
        # Telegram SDK bootstrap errors may put the token in their traceback.
        return super().format(record).replace(self.token, "[REDACTED]")


class QuestionTooLong(ValueError):
    pass


@dataclass
class Reply:
    text: str
    history: list[dict]


class ModelChat:
    """Load once; preserve the training system prompt and require an actual adapter."""

    def __init__(self, cfg, adapter, context_tokens=2048, history_turns=6):
        self.cfg = cfg
        self.history_turns = history_turns
        path = Path(adapter).resolve()
        if not (path / "adapter_config.json").is_file() or not any(
                (path / name).is_file() for name in ("adapter_model.safetensors", "adapter_model.bin")):
            raise ValueError(f"Нет готового LoRA-адаптера в {path}. Укажи сохранённый adapter или checkpoint.")
        metadata = json.loads((path / "adapter_config.json").read_text())
        if metadata.get("base_model_name_or_path") != cfg["model"]["name"]:
            raise ValueError("Базовая модель адаптера отличается от model.name в конфигурации.")
        if history_turns < 1 or context_tokens <= cfg["generation"]["max_new_tokens"]:
            raise ValueError("Нужны history_turns >= 1 и context_tokens больше max_new_tokens.")
        self.model, self.tokenizer = load_model(cfg, str(path))
        self.model.eval()
        self.model.config.use_cache = True
        self.context_tokens = min(context_tokens, self.model.config.max_position_embeddings)
        if self.context_tokens <= cfg["generation"]["max_new_tokens"]:
            raise ValueError("Окно модели меньше бюджета ответа.")

    def reply(self, history, question):
        kept = [dict(m) for m in history[-2 * self.history_turns:]]
        user = {"role": "user", "content": question}
        while True:
            messages = [{"role": "system", "content": self.cfg["system"]}] + kept + [user]
            tokens = self.tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True)
            if len(tokens) + self.cfg["generation"]["max_new_tokens"] <= self.context_tokens:
                break
            if not kept:
                raise QuestionTooLong("Вопрос слишком длинный. Сократи его и отправь ещё раз.")
            kept = kept[2:]  # Remove a whole old turn, never part of the current question.
        answer = generate(self.model, self.tokenizer, [messages], self.cfg)[0].strip()
        if not answer:
            raise RuntimeError("Empty model response")
        history = kept + [user, {"role": "assistant", "content": answer}]
        return Reply(answer, history[-2 * self.history_turns:])


def split_answer(text, limit=3500):
    """Keep even emoji-heavy output below Telegram limits without changing the text."""
    if limit < 2:
        raise ValueError("limit must be at least 2 UTF-16 units")
    chunks = []
    while text:
        units, end = 0, 0
        for char in text:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > limit:
                break
            units += size
            end += 1
        if end == len(text):
            chunks.append(text)
            break
        cut = max(text.rfind("\n", 0, end), text.rfind(" ", 0, end)) + 1
        if cut < end // 2:
            cut = end
        chunks.append(text[:cut])
        text = text[cut:]
    return chunks


@dataclass
class Session:
    history: list[dict] = field(default_factory=list)
    revision: int = 0
    busy: bool = False


class BotChat:
    """Per-chat RAM histories and one GPU worker; no prompts or answers in logs."""

    def __init__(self, engine, allowed_users=(), max_sessions=128, max_pending=8):
        self.engine = engine
        self.allowed_users = frozenset(allowed_users)
        self.sessions = OrderedDict()
        self.max_sessions = max_sessions
        self.max_pending = max_pending
        self.pending = 0
        self.gpu = asyncio.Lock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model")

    def session(self, chat_id):
        if chat_id not in self.sessions:
            while len(self.sessions) >= self.max_sessions:
                idle = next((key for key, value in self.sessions.items() if not value.busy), None)
                if idle is None:
                    raise RuntimeError("All conversation slots are occupied")
                self.sessions.pop(idle)
            self.sessions[chat_id] = Session()
        self.sessions.move_to_end(chat_id)
        return self.sessions[chat_id]

    async def authorized(self, update):
        if self.allowed_users and update.effective_user.id not in self.allowed_users:
            await update.effective_message.reply_text("Бот доступен участникам демонстрации.")
            return False
        return True

    async def start(self, update, context):
        if not await self.authorized(update):
            return
        state = self.session(update.effective_chat.id)
        state.history = []
        state.revision += 1
        await update.effective_message.reply_text(
            "👋 Решай сам\n\nОтправь вопрос — я передам его дообученной модели.\n"
            "Можно продолжать разговор следующими сообщениями.\n\n"
            "/reset — начать новый диалог\n/help — справка")

    async def reset(self, update, context):
        if not await self.authorized(update):
            return
        state = self.session(update.effective_chat.id)
        state.history = []
        state.revision += 1
        suffix = " Текущий запрос завершится без вывода ответа." if state.busy else " Отправь новый вопрос."
        await update.effective_message.reply_text("↻ Контекст сброшен." + suffix)

    async def help(self, update, context):
        if await self.authorized(update):
            await update.effective_message.reply_text(
                "Отправь текстовый вопрос. Ответит дообученная модель с сохранённым адаптером.\n\n"
                "История учитывается в пределах окна контекста. /reset очищает её. "
                "После перезапуска бота история не сохраняется.")

    async def unsupported(self, update, context):
        if await self.authorized(update):
            await update.effective_message.reply_text("Пока я принимаю только текст. Отправь вопрос сообщением.")

    async def typing(self, context, chat_id):
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                return  # Typing indicator is optional; inference can continue.
            await asyncio.sleep(4)

    async def answer(self, update, context):
        if not await self.authorized(update):
            return
        message, chat_id = update.effective_message, update.effective_chat.id
        if not message.text or not message.text.strip():
            return
        state = self.session(chat_id)
        if state.busy:
            await message.reply_text("Подожди завершения предыдущего запроса.")
            return
        if self.pending >= self.max_pending:
            await message.reply_text("Сейчас очередь заполнена. Попробуй немного позже.")
            return
        state.busy = True
        self.pending += 1
        revision = state.revision
        status, indicator = None, None
        try:
            status = await message.reply_text("⏳ В очереди…" if self.gpu.locked() else "⏳ Думаю…")
            indicator = asyncio.create_task(self.typing(context, chat_id))
            async with self.gpu:
                if revision != state.revision:
                    await status.edit_text("Диалог сброшен. Запрос отменён.")
                    return
                result = await asyncio.get_running_loop().run_in_executor(
                    self.worker, self.engine.reply, list(state.history), message.text)
            if revision != state.revision:
                await status.edit_text("Диалог сброшен. Этот ответ пропущен.")
                return
            chunks = [chunk for chunk in split_answer(result.text) if chunk.strip()]
            await status.edit_text(chunks[0], parse_mode=None)
            for chunk in chunks[1:]:
                await message.reply_text(chunk, parse_mode=None)
            if revision == state.revision:
                state.history = result.history
        except QuestionTooLong as exc:
            if status:
                await status.edit_text(str(exc))
        except Exception as exc:
            LOG.error("Request failed: %s", type(exc).__name__)
            if status:
                await status.edit_text("Не удалось получить ответ модели. Попробуй ещё раз или начни новый диалог: /reset")
        finally:
            if indicator:
                indicator.cancel()
                with suppress(asyncio.CancelledError):
                    await indicator
            state.busy = False
            self.pending -= 1

    def close(self):
        self.worker.shutdown(wait=True, cancel_futures=True)


def build_application(token, service):
    from telegram import BotCommand, Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    async def initialize(app):
        await app.bot.set_my_commands([
            BotCommand("start", "Начать разговор"),
            BotCommand("reset", "Очистить контекст"),
            BotCommand("help", "Как пользоваться ботом"),
        ])

    async def shutdown(app):
        service.close()

    async def error(update, context):
        # HTTP exception strings can contain the Bot API URL, including the token.
        LOG.error("Telegram update failed: %s", type(context.error).__name__)

    app = (Application.builder().token(token).concurrent_updates(16)
           .post_init(initialize).post_shutdown(shutdown).build())
    private = filters.ChatType.PRIVATE
    for name, callback in [("start", service.start), ("reset", service.reset), ("help", service.help)]:
        app.add_handler(CommandHandler(name, callback, filters=private))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, service.answer))
    app.add_handler(MessageHandler(private & ~filters.TEXT, service.unsupported))
    app.add_error_handler(error)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--adapter", default=os.environ.get("DPO_ADAPTER_PATH"))
    parser.add_argument("--context-tokens", type=int, default=2048)
    parser.add_argument("--history-turns", type=int, default=6)
    parser.add_argument("--max-new-tokens", type=int, help="Default: generation.max_new_tokens from config")
    args = parser.parse_args()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        parser.error("Задай TELEGRAM_BOT_TOKEN — токен от @BotFather.")
    if not args.adapter:
        parser.error("Укажи --adapter или DPO_ADAPTER_PATH — путь к сохранённому LoRA-адаптеру.")
    try:
        allowed = [int(x.strip()) for x in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if x.strip()]
        if any(x <= 0 for x in allowed):
            raise ValueError
    except ValueError:
        parser.error("TELEGRAM_ALLOWED_USER_IDS должен содержать положительные ID через запятую.")
    cfg = config(ROOT / args.config)
    if args.max_new_tokens is not None:
        if args.max_new_tokens < 1:
            parser.error("--max-new-tokens должен быть положительным.")
        cfg["generation"]["max_new_tokens"] = args.max_new_tokens
    handler = logging.StreamHandler()
    handler.setFormatter(TokenRedactingFormatter(token))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    engine = ModelChat(cfg, ROOT / args.adapter, args.context_tokens, args.history_turns)
    service = BotChat(engine, allowed_users=allowed)
    try:
        app = build_application(token, service)
        LOG.info("Trained model loaded. Starting Telegram polling.")
        app.run_polling(allowed_updates=["message"], drop_pending_updates=False)
    except Exception as exc:
        LOG.error("Не удалось запустить Telegram (%s). Проверь токен и подключение.", type(exc).__name__)
        raise SystemExit(1) from None
    finally:
        service.close()


if __name__ == "__main__":
    main()
