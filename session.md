# ChartAsst Session

**Date:** 2026-09-25  
**Project:** ChartAsst  
**Repository:** tex-node/chartasst

## Current objective

Build ChartAsst as a decision-following market-hypothesis assistant. The user defines a trading hypothesis in natural language or through the GUI; ChartAsst follows market conditions through the lifecycle and notifies the user when the hypothesis develops, confirms, reaches target, or becomes invalidated.

The product should remain alert/decision support first. Execution is optional and must remain separated from the hypothesis-following workflow.

## Architecture

```
ChartAsst UI
  ↓
Hypothesis API / GUI
  ↓
Hypothesis / condition engine
  ↓
Market context adapter
  ↓
MT5 / TradingView / other feeds
  ↓
Lifecycle state
  ↓
Events
  ↓
Telegram / email / UI
```

Navigation:
- PLAN — create weekly hypotheses
- WATCH — monitor live hypothesis state
- REVIEW — analyze what happened

## Hypothesis lifecycle

```
WATCHING
  ├── trigger → DEVELOPING
  │                ├── confirmation → CONFIRMED
  │                │                     ├── target → COMPLETED
  │                │                     └── invalidation → INVALIDATED
  │                └── invalidation → INVALIDATED
  └── invalidation → INVALIDATED
```

Additional states:
- expired
- paused

Important distinction: **INVALIDATED is not synonymous with LOSS**. A hypothesis can be invalidated before any trade is taken.

## Completed implementation

### Lifecycle and event safety
- Lifecycle state machine implemented in `app/hypothesis_state.py`.
- Legacy status normalization supported.
- Market events are idempotent by event type + market timestamp.
- Sequence enforcement prevents confirmation before trigger/developing and target before confirmation.
- Invalidation remains available from active states.
- Restart-safe duplicate handling implemented in `app/plan_matcher.py`.

### Condition engine
`app/condition_engine.py` supports:
- price_above
- close_above
- reclaim_above
- price_below
- close_below
- break_below
- cross_above
- cross_below
- bar_close_above
- bar_close_below
- event

Lifecycle gating is active:
- watching → trigger/invalidation
- developing → confirmation/invalidation
- confirmed → target/invalidation

### Market context
`app/market_adapter.py` normalizes OHLC data and resolves:
- previous day high/low
- previous week high/low
- session high/low

`app/mt5_handler.py:get_market_context()` now deliberately excludes MT5 position 0, because position 0 is the currently forming candle. Hypothesis evaluation therefore uses completed candles.

Daily and weekly reference bars are requested from position 1.

### Observer
The background hypothesis observer:
- runs every 15 seconds
- evaluates non-terminal, non-paused hypotheses
- obtains MT5 market context
- evaluates each hypothesis
- persists lifecycle events
- sends hypothesis notifications

Bar-aware runtime deduplication uses:
`(symbol, timeframe, plan_id, timestamp)`

Persistent event deduplication remains timestamp-based, making the observer safer across restarts.

### Notifications
`app/notifier.py` supports hypothesis event notifications:
- TRIGGER REACHED
- HYPOTHESIS CONFIRMED
- HYPOTHESIS INVALIDATED
- TARGET REACHED

### GUI
The current GUI provides:
- PLAN / WATCH / REVIEW navigation
- hypothesis cards
- lifecycle state indicators
- live market evidence
- hypothesis creation form
- visual condition builder
- voice input prototype
- contextual reference selection
- manual status controls
- automatic refresh

The GUI is designed to keep JSON and backend condition structures hidden from the user.

## Latest test coverage

Existing:
- `tests/test_hypothesis_lifecycle.py`
- `tests/test_condition_engine.py`

Added this session:
- `tests/test_mt5_market_context.py`

The new MT5 test verifies:
1. The forming candle is excluded.
2. The completed candle becomes the market close/price.
3. Previous close is mapped correctly.
4. Completed daily reference is used.
5. Completed weekly reference is used.
6. Daily/weekly requests use MT5 position 1.

Latest test commit:

`c33de9f6b09a717cf22585afb7cc3bd05db56087`

## Important current caveats

1. **Tests have not yet been executed in this session.** No existing GitHub Actions pytest workflow was found through repository search, so do not claim the suite is passing until an actual test run is available.
2. The observer currently obtains market context independently for each hypothesis. This is correct but can later be optimized by caching context per `(symbol, timeframe)` during each observer cycle.
3. Session high/low currently use a UTC calendar-day interpretation. A broker/session-aware implementation should replace this later.
4. `reclaim_above` currently behaves essentially as a close-above condition; true reclaim semantics should eventually require evidence of the prior state being below the level.
5. Live trading safety should not yet be described as production-complete.

## Immediate next steps

1. Execute the complete pytest suite in an environment with the repository dependencies.
2. Add a higher-level observer/evaluation integration test without starting the real daemon thread.
3. Optimize observer market-context retrieval by caching shared symbol/timeframe contexts per cycle.
4. Improve market-session handling using broker/session timezone configuration.
5. Improve `reclaim_above` semantics so reclaim requires a prior below-level state followed by a qualifying close above.
6. Continue refining the GUI so voice/graphical hypothesis creation is the primary user workflow and backend condition structures remain invisible.

## Key commits

- `ad5a700b6706c24c89145f4d4488080d453fcd6b` — market event idempotency
- `6bc74a870e74cc9c0f4f21f2e79e0fff7c2a2de7` — lifecycle sequence enforcement
- `e780a9ecfbbe433cc3708e7da89bbcdafc63193b` — restart-safe event deduplication
- `914364b95182ee2f99b567f209b962ca7c0fcad4` — market adapter
- `4edb301f05f89b1d8ef4acb8c3c6970ec239663f` — exclude MT5 forming candle
- `dac5bf99d035e31c0c2c480b656382fae89fb723` — hypothesis notifications
- `8e17f167d8c050a6a296089fb64e003606c8bb97` — GUI improvements
- `c28ab085acddba89b24d6ae01f2bfd1ec82cb823` — GUI condition-row layout fix
- `c33de9f6b09a717cf22585afb7cc3bd05db56087` — MT5 market-context integration tests
