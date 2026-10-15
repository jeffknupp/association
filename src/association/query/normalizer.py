"""What the model does once the parser reads the question: copy the names out
of it verbatim and pick one stat key - nothing else.

Everything else is read from the words by the parser, the grammar's rules
and the taggers. What code cannot replace is two things: the stat
vocabulary ("fta" is ``freeThrowsAttempted``) and the spans that are names. So
this is the whole job left to the model, and :func:`association.query.parse.read_route`
checks both - every span against the entity index, the stat against the
question's own words - before anything reads them.

Names are copied exactly as typed, never expanded or corrected. A typo is the
entity index's to read (a single near spelling is that player, said in the
answer), never the model's: a model's silent correction is how "embiid"
becomes Ben Simmons (AGENTS.md, "Why embiid specifically"). Measured over the
277 day10 wordings: 299 of 302 names copied verbatim, 0.8s a question on
qwen2.5:3b (``~/association-research/parser-greenfield/RESULT.md``).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import ollama

from .keepalive import KEEP_ALIVE


class RouterUnavailable(RuntimeError):
    """The model could not be asked at all - ollama is down, or cannot serve
    that model. Raised by :func:`normalize`.

    Distinct from ``normalize`` returning None, which means the model
    answered with something unusable. Both are refused, and neither is
    fatal; what differs is the sentence a person gets, which is the entire
    answer.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Raised by the normalizer: the router's own model call is gone.

    .. versionchanged:: 6.0.0
       Lives here, beside the one call that raises it:
       ``association.query.router`` is gone (Phase 3, step 4, slice (b)).
    """


NORMALIZER_STATS: tuple[str, ...] = (
    "points",
    "rebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "fouls",
    "minutes",
    "fieldGoalsMade",
    "fieldGoalsAttempted",
    "fieldGoalPct",
    "threePointFieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "threePointFieldGoalPct",
    "twoPointFieldGoalPct",
    "freeThrowsMade",
    "freeThrowsAttempted",
    "freeThrowPct",
    "offensiveRebounds",
    "defensiveRebounds",
    "ts_pct",
    "efg_pct",
    "usage_pct",
    "double_double",
    "triple_double",
    "netpoints",
    "netpoints_per_100",
    "netpoints_offense",
    "netpoints_defense",
    "netpoints_offense_per_100",
    "netpoints_defense_per_100",
    "wins",
    "losses",
    "record",
    "games_played",
    "shot_distance",
    "points_allowed",
    "point_differential",
    "",
)
"""The stat keys the model may choose from, ``""`` for none - the list the
normalizer was measured with. An enum, so constrained decoding cannot emit a
key nothing reads."""

NORMALIZER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"names": {"type": "array", "items": {"type": "string"}}, "stat": {"type": "string", "enum": list(NORMALIZER_STATS)}},
    "required": ["names", "stat"],
}
"""Both fields required: a slot the schema does not require is one the decoder
may never consider (AGENTS.md, on ``side``)."""

NORMALIZER_PROMPT = (
    "You extract two things from an NBA statistics question. Reply with JSON only.\n"
    '"names": every player or team the question names, copied EXACTLY as the words appear in the question (same spelling, same case, no expansion, no '
    'correction). A nickname or abbreviation stays as typed ("sga", "kd", "steph"). Empty list if none.\n'
    '"stat": the one statistic the question is about, as one key from the allowed list, or "" if the question names no specific statistic (a record, a '
    'game log, a comparison of whole lines, a fingerprint are ""). '
    "Abbreviations: fga/fgm field goals attempted/made, fta/ftm free throws, 3pm/3pa three pointers, reb rebounds, ast assists, stl steals, blk blocks, "
    "to/tov turnovers, pf fouls, ts% true shooting, usg usage, +/- point differential.\n"
    "Examples:\n"
    'Q: how many points does embiid average -> {"names":["embiid"],"stat":"points"}\n'
    'Q: show maxey\'s games against boston -> {"names":["maxey","boston"],"stat":""}\n'
    'Q: Sga games with under 14 fta in his whole career -> {"names":["Sga"],"stat":"freeThrowsAttempted"}\n'
    'Q: who led the league in assists in 2019 -> {"names":[],"stat":"assists"}\n'
    'Q: 76ers record when maxey scores 20+ points -> {"names":["76ers","maxey"],"stat":"points"}'
)
"""The prompt, verbatim as measured. Any edit moves what the model returns on
unrelated questions (AGENTS.md, "Any edit to the model's prompt"): re-measure
after one."""

NORMALIZER_NUM_CTX = 2048
"""The prompt is ~330 tokens; the window only has to hold it and one question."""


def estimate_tokens(text: str) -> int:
    """Deliberately a slight over-estimate (measured ~4.08 chars/token on a
    prompt of this project's), so a budget check errs toward failing loudly
    rather than silently truncating: ollama truncates an over-length prompt
    head-first and in silence.

    .. versionadded:: 5.0.0
       Moved from ``association.query.prompt``, which went with the agent.
    """
    return len(text) // 4


@dataclass(frozen=True)
class Normalized:
    """The model's two answers: the spans it read as names, and a stat key
    (``""`` for none). Neither is trusted - see the module docstring.

    .. versionadded:: 5.0.0
    """

    names: list[str] = field(default_factory=list)
    stat: str = ""


def normalize(model: str, question: str) -> Normalized | None:
    """The names and stat ``model`` reads in ``question``, or None when it
    replies with something that is not the schema's object. Raises
    :class:`RouterUnavailable` when the model could
    not be asked at all - two different sentences, since the reader has to
    be sent to the server rather than to their question.

    .. versionadded:: 5.0.0
    """
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "system", "content": NORMALIZER_PROMPT}, {"role": "user", "content": f"Q: {question}"}],
            format=NORMALIZER_SCHEMA,
            keep_alive=KEEP_ALIVE,
            options={"num_ctx": NORMALIZER_NUM_CTX, "temperature": 0},
        )
        raw = json.loads(response.message.content or "{}")
    except ConnectionError as exc:
        raise RouterUnavailable(f"ollama is not answering, so the model {model!r} could not be asked") from exc
    except ollama.ResponseError as exc:
        raise RouterUnavailable(f"ollama could not serve the model {model!r}: {exc}") from exc
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    names = [name.strip() for name in raw.get("names") or [] if isinstance(name, str) and name.strip()]
    stat = raw.get("stat")
    return Normalized(names=names, stat=stat if isinstance(stat, str) and stat in NORMALIZER_STATS else "")
