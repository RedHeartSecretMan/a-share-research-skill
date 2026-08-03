# Analysis boundary

Read this reference when a research question calls for interpretation, could produce a research judgment or conditional trigger, or includes an external rating, target price, or buy/sell view.

## Decide whether to interpret

Use the CLI result's overall status and the user's question to choose the response branch:

- For an overall `blocked` result, state the blocking evidence gap and what evidence is needed to continue. Stop before forming a research judgment or trigger level.
- For identity-only lookup, contract validation, and other questions asking only for direct evidence, answer the requested evidence without adding an unsolicited judgment.
- Otherwise, after presenting the evidence, provide a useful research judgment when interpretation would answer the user's question.

## Intraday Agent analysis

For `intraday_market_signal`, the deterministic CLI only forms a dated, canonical-security snapshot and its evidence boundary. A `limited` result may support a clearly labelled **Agent analysis** or **Agent inference** when the judgment stays within the returned session, price type, units, timing, conflicts, and limitations. Do not turn it into a trend, catalyst, trading signal, price target, or action instruction. A `blocked` result reports the missing or conflicting evidence and stops before any research judgment.

## Evidence-constrained intraday replay prediction

`intraday_replay` remains deterministic. A historical query or replay request is not a prediction request, and its JSON does not contain prediction fields or Agent views. Route to this section only when the user explicitly asks for a future judgment. Present the source facts and deterministic calculations first, and keep replay analysis and prediction as separate Agent-generated layers.

The minimum evidence floor is canonical identity, a non-blocked replay with a usable close or closing stage, 20 complete daily trading sessions through the replay date, aligned security/date/unadjusted price semantics/close, and no unresolved core source conflict. Refuse the prediction and state the evidence needed when coverage is indeterminate, the close is missing, the whole morning or whole afternoon is missing, daily evidence is incomplete, or there is a confirmed suspension. A bounded partial replay can support only a `limited` prediction when its close remains usable; list every gap and the affected judgment.

Use two independent horizons: the next trading day and the next 5 trading days. Each horizon must contain one primary scenario selected from continuation, range, or reversal, plus an upside alternative and a downside alternative. Each scenario must state its horizon, evidence basis, supporting evidence, opposing evidence, assumptions, observable triggers, invalidation conditions, and uncertainty. The optional context (announcements, news, fund flow, capital events, valuation, industry, and broader market) may enhance the analysis; if it is missing, disclose that limitation and restrict the claim to price behavior rather than inventing a cause or “main force” intent.

No exact probabilities or pseudo-statistical confidence are allowed without a named reproducible model with genuine out-of-sample validation. Scenario prediction is research inference, not investment advice: do not generate a project or Agent rating, target price, direct buy/sell/hold instruction, position sizing, stop-loss order, execution timing, alerts, orders, or automatic trading.

## Research judgment

Label a research judgment as **Agent inference**. State the evidence, explicit comparison benchmark, assumptions, research horizon, uncertainty, material risks, evidence supporting and opposing the interpretation, and the conditions that would invalidate it. Keep source facts, project calculations, attributed opinions, market signals, and Agent inference visibly separate.

Label arithmetic already returned by the CLI as a project calculation. If the Agent derives an additional simple value from returned operands, label it as an **Agent calculation**, show its formula and operands, and never attribute it to the project or a source.

Express valuation comparatively: say that a metric is higher or lower than a named peer, historical range, or user-supplied scenario under a stated basis. Words such as “cheap” or “expensive” may appear only as qualified interpretation against that explicit benchmark, never as an unqualified fact or objective fair-value conclusion.

## Conditional trigger level

A conditional trigger level is allowed only when its value is reproducibly derived from cited evidence plus an explicit rule, horizon, and scenario assumption. State the operands and calculation, the event that causes reassessment, and the conditions—such as a corporate action or changed price basis—that require the level to be discarded and recalculated.

Phrase the level as a condition for reassessing the research judgment. It is Agent analysis, not a prediction, order, or promise of future performance.

## Attributed external opinions

Present an external rating, target price, or buy/sell view only as a clearly attributed opinion. Include its author or institution, publication time, source locator, applicable horizon when available, and source limitations. Evidence that an opinion was published does not establish its conclusion as fact or make it the Agent's judgment.

## Investment action boundary

Keep the output in research-assistance scope: evidence-backed comparisons, scenarios, risks, invalidation conditions, conditional triggers, and follow-up research suggestions. Do not generate a project or Agent rating, price target, direct buy/sell/hold instruction, position-sizing advice, stop-loss order, or automated-trading instruction.

End applicable research judgments with a concise risk statement that identifies the analysis as Agent-generated and leaves the final investment decision with the researcher. That disclosure supplements the evidence and method; it does not replace them.
