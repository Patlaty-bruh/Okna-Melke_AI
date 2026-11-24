"""NLU-слой над агентом: заготовленные ответы до вызова модели.

Порядок разбора сообщения:
  1. короткие реплики («да», «нет», «не знаю») — сразу агенту, только он видит контекст;
  2. смолток («привет», «как дела», «спасибо») — заготовленный ответ из кода;
  3. похожий вопрос в FAQ — заготовленный ответ со ссылкой на источник;
  4. всё остальное — агенту.
"""

import logging
import re
from functools import lru_cache

from nlu.store import FaqStore
from rag.config import Settings
from rag.embeddings import Embedder, get_embedder

logger = logging.getLogger(__name__)

GREETING_REPLY = (
    "Здравствуйте! Я ИИ-помощник компании Melke: помогу подобрать окна, расскажу о профилях "
    "и стеклопакетах, фурнитуре, гарантии, доставке и монтаже. Что вас интересует?"
)
HOW_ARE_YOU_REPLY = (
    "Спасибо, всё в порядке! Готов помочь с окнами: подбор профиля и стеклопакета, цены, "
    "гарантия, доставка и монтаж. С чего начнём?"
)
WHO_ARE_YOU_REPLY = (
    "Я ИИ-помощник компании Melke. Отвечаю на вопросы об окнах, помогаю подобрать профиль, "
    "стеклопакет и фурнитуру, подсказываю по гарантии, доставке и монтажу. "
    "Если вопрос сложный, передам его менеджеру."
)
THANKS_REPLY = "Пожалуйста! Если появятся вопросы об окнах — спрашивайте."
GOODBYE_REPLY = "До свидания! Если понадобится помощь с окнами, обращайтесь."

# Реплики, которые без контекста диалога не имеют смысла: их разбирает модель.
SHORT_REPLIES = frozenset(
    {
        "да",
        "нет",
        "ага",
        "угу",
        "неа",
        "ок",
        "окей",
        "хорошо",
        "ладно",
        "конечно",
        "возможно",
        "может",
        "может быть",
        "наверное",
        "не знаю",
        "не уверен",
        "не уверена",
        "скорее да",
        "скорее нет",
        "без разницы",
        "как хотите",
        "не важно",
        "пока нет",
        "еще нет",
        "ещё нет",
    }
)

# Слова, которые могут стоять рядом с приветствием и не делают сообщение вопросом.
_FILLER_WORDS = frozenset(
    {
        "как",
        "дела",
        "вы",
        "ваши",
        "вам",
        "тоже",
        "взаимно",
        "настроение",
        "жизнь",
        "друзья",
        "всем",
        "и",
        "большое",
        "огромное",
        "за",
        "помощь",
        "консультацию",
        "ответ",
        "пока",
        "до",
        "свидания",
        "добрый",
        "доброе",
        "доброго",
        "день",
        "утро",
        "вечер",
        "времени",
        "суток",
    }
)

_GREETING_RE = re.compile(
    r"^(привет|приветик|приветствую|здравствуйте|здравствуй|здрасьте|здорово|хай|hi|hello|"
    r"добрый день|доброе утро|добрый вечер|доброго дня|доброго времени суток)"
    r"\b[\s,!.]*(?P<rest>.*)$"
)
_THANKS_RE = re.compile(r"^(спасибо|благодарю|спс|thanks|thank you)\b[\s,!.]*(?P<rest>.*)$")
_GOODBYE_RE = re.compile(r"^(пока|до свидания|до встречи|всего доброго|всего хорошего|bye)"
                         r"\b[\s,!.]*(?P<rest>.*)$")
_HOW_ARE_YOU_RE = re.compile(
    r"^(как дела|как вы|как у вас дела|как ваши дела|как настроение|как жизнь|как сам|как сами)"
    r"[\s!?.,]*$"
)
_WHO_ARE_YOU_RE = re.compile(
    r"^(ты кто|кто ты|вы кто|кто вы|ты бот|вы бот|ты робот|вы робот|ты человек|вы человек)"
    r"[\s!?.,]*$"
)

_PUNCTUATION_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES_RE = re.compile(r"\s+", re.UNICODE)


class NluLayer:
    """Готовые ответы на типовые сообщения: смолток и вопросы из FAQ."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings

    def reply(self, message: str) -> str | None:
        """Возвращает заготовленный ответ или None, если обрабатывать должен агент."""

        normalized = _normalize(message)
        if not normalized or normalized in SHORT_REPLIES:
            return None

        small_talk = _small_talk(normalized)
        if small_talk is not None:
            logger.info("NLU: заготовленный ответ на смолток")
            return small_talk

        if not self._settings.faq_enabled:
            return None
        return self._faq_reply(message)

    def _faq_reply(self, message: str) -> str | None:
        try:
            vector = self._embedder().embed_query(message)
            with FaqStore(self._settings) as store:
                matches = store.search(
                    vector, self._settings.faq_match_threshold, limit=1
                )
        except Exception as error:
            # Без БД или модели слой просто не срабатывает: сообщение обработает агент.
            logger.warning("NLU: поиск по FAQ недоступен (%s)", type(error).__name__)
            return None

        if not matches:
            return None
        match = matches[0]
        logger.info("NLU: ответ из FAQ %s (score %.3f)", match.entry_id, match.score)
        return _with_source(match.answer, match.source_url)

    def _embedder(self) -> Embedder:
        return get_embedder(self._settings)


def _normalize(text: str) -> str:
    """Приводит сообщение к виду для сравнения с заготовками."""

    lowered = text.lower().replace("ё", "е").strip()
    without_punctuation = _PUNCTUATION_RE.sub(" ", lowered)
    return _SPACES_RE.sub(" ", without_punctuation).strip()


def _small_talk(normalized: str) -> str | None:
    """Заготовленный ответ на приветствие, благодарность или прощание."""

    if _HOW_ARE_YOU_RE.match(normalized):
        return HOW_ARE_YOU_REPLY
    if _WHO_ARE_YOU_RE.match(normalized):
        return WHO_ARE_YOU_REPLY

    for pattern, reply in (
        (_GREETING_RE, GREETING_REPLY),
        (_THANKS_RE, THANKS_REPLY),
        (_GOODBYE_RE, GOODBYE_REPLY),
    ):
        found = pattern.match(normalized)
        if found is None:
            continue
        rest = _normalize(found.group("rest"))
        # «Привет, сколько стоит окно?» — это вопрос, его должен разбирать агент.
        if not rest or all(word in _FILLER_WORDS for word in rest.split()):
            return reply
    return None


def _with_source(answer: str, source_url: str | None) -> str:
    if not source_url:
        return answer
    return f"{answer}\n\nПодробнее: {source_url}"


@lru_cache(maxsize=1)
def get_layer() -> NluLayer:
    """NLU-слой создаётся один раз на процесс."""

    return NluLayer(Settings())
