import asyncio
import copy
import importlib.util
import json
import logging
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from telegram_bot import BotChat, ModelChat, QuestionTooLong, Reply, TokenRedactingFormatter, build_application, split_answer


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adapter = Path(self.tmp.name)
        (self.adapter / "adapter_config.json").write_text(json.dumps({"base_model_name_or_path": "test-model"}))
        (self.adapter / "adapter_model.safetensors").write_bytes(b"mock weights")
        self.cfg = {"model": {"name": "test-model"}, "system": "S",
                    "refusal_system": "DO NOT ADD THIS SYSTEM PROMPT", "generation": {"max_new_tokens": 2}}
        self.model = SimpleNamespace(config=SimpleNamespace(max_position_embeddings=64), eval=lambda: None)
        self.tokenizer = SimpleNamespace(apply_chat_template=lambda messages, **kwargs: [0] * sum(len(m["content"]) for m in messages))

    def tearDown(self):
        self.tmp.cleanup()

    def test_adapter_is_required_before_loading_base_weights(self):
        (self.adapter / "adapter_model.safetensors").unlink()
        with patch("telegram_bot.load_model") as load:
            with self.assertRaisesRegex(ValueError, "LoRA"):
                ModelChat(self.cfg, self.adapter)
            load.assert_not_called()

    def test_adapter_base_mismatch_fails_before_loading(self):
        (self.adapter / "adapter_config.json").write_text('{"base_model_name_or_path": "other-model"}')
        with patch("telegram_bot.load_model") as load:
            with self.assertRaisesRegex(ValueError, "отличается"):
                ModelChat(self.cfg, self.adapter)
            load.assert_not_called()

    def test_adapter_used_and_context_drops_whole_old_turns(self):
        history = [{"role": role, "content": text} for role, text in
                   [("user", "aaaa"), ("assistant", "bbbb"), ("user", "cc"), ("assistant", "dd")]]
        original = copy.deepcopy(history)
        with patch("telegram_bot.load_model", return_value=(self.model, self.tokenizer)) as load:
            engine = ModelChat(self.cfg, self.adapter, context_tokens=16)
        load.assert_called_once_with(self.cfg, str(self.adapter.resolve()))
        with patch("telegram_bot.generate", return_value=["ok"]) as generate:
            result = engine.reply(history, "new")
        used = generate.call_args.args[2][0]
        self.assertEqual(used, [{"role": "system", "content": "S"}] + history[2:] + [{"role": "user", "content": "new"}])
        self.assertEqual(result.history[-1], {"role": "assistant", "content": "ok"})
        self.assertEqual(history, original)

    def test_current_question_is_not_truncated(self):
        with patch("telegram_bot.load_model", return_value=(self.model, self.tokenizer)):
            engine = ModelChat(self.cfg, self.adapter, context_tokens=16)
        with patch("telegram_bot.generate") as generate:
            with self.assertRaises(QuestionTooLong):
                engine.reply([], "x" * 20)
            generate.assert_not_called()

    def test_unicode_splitting_preserves_text_and_limits(self):
        text = "<b>Текст</b>\n" + "🙂" * 4000 + "\n" + "word " * 100
        chunks = split_answer(text, limit=50)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(c.encode("utf-16-le")) // 2 <= 50 for c in chunks))

    def test_sdk_error_tracebacks_cannot_expose_bot_token(self):
        token = "123456:private-token"
        error = RuntimeError("The token " + token + " was rejected")
        record = logging.LogRecord("telegram", logging.ERROR, "bot.py", 1,
                                   "URL https://api.telegram.org/bot%s/getMe", (token,),
                                   (RuntimeError, error, None))
        rendered = TokenRedactingFormatter(token).format(record)
        self.assertNotIn(token, rendered)
        self.assertIn("[REDACTED]", rendered)


class Sent:
    def __init__(self, text):
        self.text = text

    async def edit_text(self, text, **kwargs):
        self.text = text


class Message:
    def __init__(self, text):
        self.text, self.sent = text, []

    async def reply_text(self, text, **kwargs):
        item = Sent(text)
        self.sent.append(item)
        return item


def update(chat_id, text):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id),
                           effective_user=SimpleNamespace(id=chat_id), effective_message=Message(text))


class EchoEngine:
    def __init__(self):
        self.calls, self.active, self.peak = [], 0, 0

    def reply(self, history, question):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.calls.append((copy.deepcopy(history), question))
        time.sleep(.01)
        self.active -= 1
        answer = "Ответ: " + question
        return Reply(answer, history + [{"role": "user", "content": question}, {"role": "assistant", "content": answer}])


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = EchoEngine()
        self.service = BotChat(self.engine)
        self.context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))

    def tearDown(self):
        self.service.close()

    async def test_two_users_have_separate_histories_and_gpu_is_serial(self):
        a, b = update(1, "first"), update(2, "other")
        await asyncio.gather(self.service.answer(a, self.context), self.service.answer(b, self.context))
        await self.service.answer(update(1, "follow-up"), self.context)
        previous = next(history for history, q in self.engine.calls if q == "follow-up")
        self.assertEqual(previous[0]["content"], "first")
        self.assertNotIn("other", str(previous))
        self.assertEqual(self.engine.peak, 1)
        self.assertEqual(self.service.pending, 0)

    async def test_reset_discards_an_inflight_answer(self):
        started, release = threading.Event(), threading.Event()
        def reply(history, question):
            started.set()
            release.wait(3)
            return Reply("OBSOLETE ANSWER", [{"role": "user", "content": question}])
        self.engine.reply = reply
        u = update(1, "question")
        task = asyncio.create_task(self.service.answer(u, self.context))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            await self.service.reset(u, self.context)
        finally:
            release.set()
            await task
        self.assertEqual(self.service.session(1).history, [])
        self.assertNotIn("OBSOLETE ANSWER", [p.text for p in u.effective_message.sent])
        self.assertFalse(self.service.session(1).busy)

    async def test_model_error_does_not_poison_history_or_busy_flag(self):
        with patch.object(self.engine, "reply", side_effect=RuntimeError("failure")):
            with self.assertLogs("telegram_bot", level="ERROR"):
                await self.service.answer(update(1, "question"), self.context)
        self.assertEqual(self.service.session(1).history, [])
        self.assertFalse(self.service.session(1).busy)
        await self.service.answer(update(1, "retry"), self.context)
        self.assertEqual(self.service.session(1).history[0]["content"], "retry")

    async def test_allowlist_blocks_inference(self):
        self.service.allowed_users = {42}
        await self.service.answer(update(1, "question"), self.context)
        self.assertEqual(self.engine.calls, [])

    async def test_same_chat_does_not_queue_duplicate_inflight_requests(self):
        state = self.service.session(1)
        state.busy = True
        await self.service.answer(update(1, "question"), self.context)
        self.assertEqual(self.engine.calls, [])


@unittest.skipUnless(importlib.util.find_spec("telegram"), "Install requirements-bot.txt for SDK wiring test")
class TelegramWiringTests(unittest.TestCase):
    def test_application_builds_without_network_calls(self):
        service = BotChat(EchoEngine())
        try:
            app = build_application("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk", service)
            self.assertEqual(len(app.handlers[0]), 5)
            self.assertTrue(app.error_handlers)
        finally:
            service.close()
