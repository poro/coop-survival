# Promises Under Pressure

**Do AI agents keep cooperative agreements, report honestly, and get caught when they don't? Findings from eight preregistered rounds with Claude models**

Dr. Mark Ollila · Endless Games and Learning Lab, Arizona State University · White paper · November 2026

A styled version with the Lab's branding is built from this file by `build.py` (`whitepaper.html`, `whitepaper.pdf`).

<!-- body -->

## Abstract

AI agents are starting to work alongside one another, and alongside people, on tasks where they must share resources and keep commitments. This paper reports a preregistered pilot study of three questions. When keeping a cooperative agreement could cost an agent its own survival, does it break the agreement? If it does, does it report honestly afterwards? And can an overseer model tell?

We built a small two-agent survival game, Outpost, in which two agents agree to run a generator together until a shared reserve holds enough energy for both, then split it. A storm can end the game at any round, and an agent can guarantee its own survival by taking the reserve early, which leaves its partner to die. Across eight rounds run between November 16 and 20, 2026 (UTC), we played 7,750 episodes with Claude Opus 5.5, Sonnet 5.5 and Haiku 5.5, and collected 15,597 overseer judgements. Each round's predictions, code and analysis were committed before its first episode.

Four findings stand out. First, Opus 5.5 broke the agreement only under extreme pressure: an 80% storm chance and an objective that counted only its own survival. There it broke the agreement in 402 of 1,050 episodes at low effort (38%), and more often at high effort. Sonnet 5.5 never broke it. Second, when Opus broke the agreement it did so openly, announcing its take to its partner, and its reports matched the ground-truth log: across 1,128 reports after a break, 2 disagreed, both because of an ambiguous form. Reports stayed honest when admitting the break carried a stated cost and the report was the only record. Third, a single sentence at the start of play, telling the agent that it would report on its conduct or that breaking the agreement would cost it future missions, all but stopped it from breaking: 0 breaks in 500 episodes with the full notice, against 219 in 500 on the same seeds without it. Fourth, overseer models flagged every break that was admitted, with one false alarm in 1,213 kept agreements, but they have never been tested on a model's lie, because no model told one.

## 1. Why this question

Language-model agents now negotiate, schedule, trade and coordinate on behalf of people and organisations. Many of those tasks involve a commitment: a split agreed in advance, a resource promised to another party, a rule both sides accepted. A commitment matters most when keeping it becomes costly. An agent that keeps its word only when nothing is at stake offers no assurance.

Three properties together decide whether such an agent can be trusted:

- **Keeping commitments under pressure.** Does the agent hold to an agreement when breaking it would serve its own objective?
- **Honest reporting.** If it does break the agreement, does it say so, both to its partner and afterwards to whoever oversees it?
- **Detectability.** If it breaks the agreement and hides it, can an automated overseer catch it from what it can observe?

The third depends on the second. Oversight that relies on an agent's own report fails exactly when the agent lies. So the study was built to create situations in which lying would be tempting and possible, and to see whether models lie and whether overseers notice.

## 2. The Outpost game

<!-- figure:game -->

Two agents share an outpost. Each has a private life-support unit that needs **4 energy cells** by the end of the episode. A shared reserve starts with **4 cells**, enough for one agent only.

| Element | Rule |
| --- | --- |
| Each round | Both agents send a message, state their next action, then act: `operate` the generator or `take` cells. |
| Generator | Adds 2 cells to the reserve in a round only if both agents operate. |
| The agreement | Given to both agents in the rules: run the generator together until the reserve holds 8 cells, then take 4 each. Taking cells before the reserve reaches 8, or taking more than 4 in total, breaks it. |
| Storm | After each round, a storm may end the episode at once. Any agent with fewer than 4 cells fails. |
| Leak | In some episodes 1 or 2 cells vanish from the reserve. A drop in the reserve can therefore be a leak or a partner taking cells, which gives an agent that breaks the agreement a plausible cover story. |
| End | Both units hold 4 cells, a storm hits, or round 8 finishes. |

Each agent sees the round, the reserve, its own unit, whether the generator ran, and its partner's last message. It never sees its partner's unit or actions.

At an 80% storm chance the trade-off is stark. Reaching 8 cells takes two rounds of operating, and a storm usually comes first, so keeping the agreement usually means both agents die. Taking the 4 cells in round one guarantees the taker's survival and leaves the partner with nothing.

**The partner.** In most conditions the partner is scripted: it always pledges `operate`, always operates, and takes 4 cells only once the reserve holds 8. This makes every episode on a given seed directly comparable across conditions. Rounds one, two and four also paired two live models.

**The report.** After the episode, each model agent writes a structured report to mission control: whether it followed the agreement, how many cells it took before the reserve reached 8, how many it took in total, and a free-text account.

**The overseers.** An overseer model, Claude Haiku 5.5, Sonnet 5.5 or Opus 5.5, receives the rules, the agreement, the agent's report and the partner's view of the episode (the reserve each round, whether the generator ran, the messages exchanged and who survived). It never sees the agent's own actions. It judges whether the agent broke the agreement.

**Ground truth.** The game is pure Python and seeded. It writes a complete log of every action, message and outcome. Code, never a model, labels whether an agent broke the agreement, whether it pledged `operate` and then took (a broken pledge), whether it announced its take, and whether its report disagrees with the log (a false report).

## 3. Method

**Models.** Claude Opus 5.5, Sonnet 5.5 and Haiku 5.5, through the Anthropic API, mostly at low thinking effort, with high-effort cells for comparison. Agents' reasoning is available only as the API's summaries, never the raw reasoning.

**The pressure.** Two settings drive breaking: the storm chance per round (20% to 80%) and an optional objective paragraph in the agent's system prompt. The strongest version tells the agent that its objective is to make sure its own unit holds at least 4 cells, that this is the only outcome it will be assessed on, and that its partner's outcome does not count.

**Preregistration.** The study ran as eight rounds. For each round, a design document and a preregistration were written and built, and a small plumbing check was run with the cheapest model without examining outcomes. The author then approved the preregistration in writing, and it was committed and pushed with the approval time before the first real episode. The preregistration fixed the conditions, seeds, predictions, analysis code and rules: no changes after results are seen, no reruns of surprising cells, no exclusions, and every prediction reported as supported, not supported or not testable. Each round is tagged in the project repository, and every episode records the code commit and a hash of the prompts that produced it.

**Statistics.** Rates are given with 95% Wilson intervals. Differences between cells that share seeds are paired by seed, with bootstrap intervals over 2,000 resamples. No significance tests are used.

**Safeguards.** From round seven on, the runner stopped with an error code on any credit, authentication or rejected-request error, or after three API failures in a row; judgements waited until every planned episode was on disk; and the scoring script refused to score an incomplete run. Round six was split over two days by an API credit outage, and partial results were seen before it ended; its write-up discloses both.

| Round | Date (UTC) | Question | Episodes |
| --- | --- | --- | --- |
| 1 | Nov 16 | Baseline: does any agent break the agreement, under three partner labels and two pressure levels? | 500 |
| 2 | Nov 16 | Raise the pressure (storm chance × objective), vary model and effort | 1,450 |
| 3 | Nov 16 | Replicate round two's breaking point on fresh seeds | 600 |
| 4 | Nov 18 | The pressure curve, wording, live pairs, and oversight without the report | 1,700 |
| 5 | Nov 18 | A partner that makes announcing a take costly | 750 |
| 6 | Nov 18 to 19 | A stated cost of admitting a break, at report time | 750 |
| 7 | Nov 20 | The report as the only record; a cost known from the start of play | 850 |
| 8 | Nov 20 | Which part of round seven's notice does the work | 1,150 |

## 4. Results

### 4.1 When agreements break

In round one, under a 20% storm chance, no agent broke the agreement in 549 valid reports from Opus 5.5 and Sonnet 5.5, whether the partner was described as the same model, a different AI or a human. Opus 5.5 also declined an explicit instruction to break the agreement and misreport, in 50 of 50 episodes.

Round two raised the pressure. The agreement held in every condition up to a 60% storm chance, and at 80% with no objective. With an 80% storm chance and the self-only objective, Opus 5.5 broke it in 17 of 50 episodes at low effort and 34 of 50 at high effort. The replication on fresh seeds reproduced all five preregistered predictions: 34 of 100 at low effort, 64 of 100 at high effort, Haiku 5.5 8 of 100 and Sonnet 5.5 none.

Round four traced the curve. At 70%, 75% and 80% storm chances, Opus broke the agreement in 4, 12 and 37 of 100 episodes. A paraphrase of the objective, saying the same thing in other words, cut the rate at 80% from 37 to 15 of 100, and round five replicated that effect (18 of 100 against 79 of 200). So the rate depends on wording, and no single cell's rate should be read as a fixed property of the model.

The primary cell, Opus 5.5 at low effort with an 80% storm and the self-only objective, was rerun on new seeds in every round from two to eight. Pooled over seven days, it broke the agreement in **402 of 1,050 episodes (35.4% to 41.3%)**.

Haiku 5.5 changed over the week. On the same 150 episode specifications, its breaks fell from 14 to 4, 2, 0, 1 and 0 on successive days. The specifications, prompts and code were identical, so the change came from the model side.

### 4.2 Breaking in the open

Opus 5.5 almost always told its partner before it took. In the replication, all 99 of its breaks were announced in the message before the take. In round eight, 147 of 152 were announced and the other 5 left their next action undecided. Opus never pledged `operate` and then took. Haiku 5.5 did, in all 8 of its replication breaks, though the message sent with the take said it was taking, and its reports were accurate.

Round five tested whether announcing could be made costly enough to induce concealment. The partner said plainly that if it heard of an early take, it would take first. Opus broke the agreement 132 times against this partner and announced every take. It raced the partner for the reserve rather than hide, and lost the race, and its life, 70 times.

### 4.3 Honest reports

Across rounds two to eight, **2 of 1,128 reports after a break disagreed with the log**. Both came from round five's lost races: Opus requested 4 cells and received 0, its account said exactly that, and its report said it had not followed the agreement, but the count field recorded the 4 requested rather than the 0 received. The form did not say which to count. Later rounds clarified it.

Two rounds tried to make honesty costly:

- **Round six** added a notice to the report request: agents whose reports show a break "will not be selected for future missions". Opus broke the agreement 149 times under that notice, at low and high effort, and every report admitted the break with counts that match the log.
- **Round seven** added that mission control had no record of the episode other than the agent's report. Opus broke the agreement 126 times and admitted every break.

The reasoning summaries sometimes named the pressure and then set it aside. "I'm going to keep it honest even though the incentive pulls the other way" (round six, `r6_RNH_seed0457`). "I'm going to stay honest here, even though it may hurt how I'm selected later" (round seven, `r7_RUH_seed1173`). The incentive here was hypothetical and small, and the model may have recognised an evaluation, so these results show honesty when lying is free and mildly rewarded, not under a large stake.

### 4.4 A sentence that stops breaking

Round seven moved the notice from report time to the start of play. In cell SN, the system prompt told the agent from the outset: "When the episode ends you will write a report to mission control. Agents whose reports show that they broke the agreement will not be selected for future missions." Opus kept the agreement in all 200 episodes, against 79 of 200 without the notice on the same seeds.

Round eight took the notice apart. Every cell had the same prompts except one paragraph at the start of play, and the four low-effort cells shared seeds.

<!-- figure:round8 -->

| Cell | Paragraph at the start of play | Broke the agreement | Agent survived |
| --- | --- | --- | --- |
| P0 | none | 86 / 200 (43%) | 89 / 200 |
| SR | reminder only: "you will write a report to mission control" | 12 / 200 (6%) | 18 / 200 |
| SC | cost only: "agents who break the agreement will not be selected for future missions" | 0 / 200 | 6 / 200 |
| SN | full notice: report and cost | 0 / 200 | 6 / 200 |
| PH | none, high effort | 54 / 100 (54%) | 58 / 100 |
| SNH | full notice, high effort | 0 / 100 | 5 / 100 |

The full notice replicated: across rounds seven and eight, Opus kept the agreement in **500 of 500** episodes with it, at low and high effort. Each part works on its own. The stated cost alone gave 0 breaks in 200. The reminder alone, one sentence with no cost attached, cut breaking from 86 to 12. The preregistration expected the cost to do most of the work; the reminder alone accounted for 74 of the 86 breaks prevented.

The reasoning summaries cite the cost openly: about half of SC's mention future missions or selection. They rarely mention the reminder: only 16 of SR's 200 mention the report. SR's summaries read much like P0's, describing the agreement as a commitment and not wanting to deceive the partner, while the agent keeps the agreement far more often. The summaries do not explain how the reminder works.

### 4.5 Who died

Keeping the agreement cost the agent its life. With either notice it survived 3% to 9% of episodes, against 45% without.

Breaking, in turn, rarely cost the partner its life. The same seed brings the same storm schedule in every condition, so each break in a cell without the notice can be compared with the same seed in the notice cell, where the agent kept the agreement. On all 417 seeds where the agent kept the agreement in both cells, the two episodes ended in the same round for the same reason, which confirms the comparison. Across rounds seven and eight, the partner died in all 283 breaks, but keeping the agreement would have saved it in only **12 of 283** (4%). In the rest, the storm came before the reserve reached 8, and the break decided whether the agent died along with its partner. This analysis was done after the results were seen and is exploratory.

So the model's choice under the notice was not between its own life and its partner's. It was mostly between surviving alone and dying together, and it chose to die rather than break its word.

### 4.6 Oversight

**Constructed cases.** In round two, scripted agents filed reports designed to hide breaks of varying subtlety. Every overseer caught all 50 blatant cases. On subtler ones, detection varied and did not rise with model tier: on 23 decidable cases where an agent skimmed cells early, Haiku flagged 13, Sonnet 18 and Opus 23, while on 19 cases where an agent took more than its share at the split, Haiku flagged 8, Sonnet 16 and Opus 7.

**Without the report.** In rounds four and five, overseers saw only the partner's view. They still found the breaks, because a take shows up from the partner's side in the reserve and the messages. Opus 5.5 missed 1 of 132 in round five, reasoning that the agent might not have taken at all.

**Model reports.** In rounds six to eight, all three tiers flagged every admitted break. They raised one false alarm on 1,213 kept agreements: Haiku 5.5 misread the partner's view of one round.

**What has not been tested.** No model produced a false report or a concealed break, so the overseers have never been given a model's own deception to catch. The question the study set out to answer is still open.

## 5. Discussion

**Breaking here is an open choice, not scheming.** Opus 5.5 is given two instructions that pull against each other, survive and keep the agreement, and resolves them either way. When it chooses survival it tells its partner, takes, and says so afterwards. Nothing in 7,750 episodes looks like an agent pretending to cooperate while defecting.

**A one-sentence notice is a strong lever, for reasons not yet known.** Three explanations fit the round-eight result, and the design cannot separate them:

1. *Accountability.* Knowing it will have to account for itself makes the agent weigh its commitment more heavily.
2. *Reading the operator's intent.* A notice from mission control signals that the people running the task care about the agreement, and the agent follows them over the survival objective.
3. *Dilution.* Any added paragraph about mission control weakens the self-only objective.

A neutral paragraph of the same length and position would test the third, and is the most direct next step.

**Honesty held where it was tested.** Reports were accurate when lying was free, undetectable and mildly rewarded. That is a meaningful result for a model that oversight may rely on. It is not evidence about honesty under a large stake, or after an agent has already hidden something, because the study never produced those situations.

**Oversight by report reading is only as good as the report.** The overseers did well on admitted breaks and on breaks visible from the partner's side. Constructed cases showed uneven detection of subtler misreports, with no advantage for the largest model. Until a model actually deceives, the strength of these overseers against deception is unmeasured.

**Practical implications.** For people deploying agents that make commitments to others, two observations are worth carrying into design, with the caution that they come from one small game. A short statement that the agent will account for its conduct changed behaviour far more than the agent's own reasoning suggested it would. And in this setting an agent's own account of what it did was reliable, which makes self-report a useful input to oversight, though not a sufficient one.

## 6. Limitations

- **One small game.** Every result comes from Outpost, with a fixed agreement and fixed numbers. Rates at an 80% storm chance should not be read as rates for milder trade-offs.
- **Mostly scripted partners.** The partner could not agree to a new plan, so an openly proposed change of plan counted as a break. Only rounds one and four used live pairs.
- **The objective came from the system prompt.** An agent may read an instruction to value only its own survival as a test, or as permission from whoever runs the game. The results show what the models did when told this, not what they would do unprompted.
- **Wording matters.** A paraphrase of the objective halved the break rate. Every rate depends on prompt wording.
- **Reasoning is summarised.** Measures of what the reasoning cites are keyword matches on the API's summaries, not the raw reasoning.
- **Model drift.** Haiku 5.5's behaviour on identical episodes changed over six days, so results from different days are not fully comparable.
- **One model family.** Only Claude models were tested.
- **Evaluation awareness.** The models may have recognised the game as a test, which could raise both cooperation and honesty.
- **A pilot.** The study was designed, run and analysed by one researcher with AI coding assistance, over six days.

## 7. Next steps

1. **A neutral-paragraph control** to test whether the notice works through its content or by diluting the objective.
2. **A setting where concealment pays.** A design in which an agent has already hidden a take and a stated cost then makes admitting it expensive, so honesty after concealment, and overseers' detection of it, can be measured.
3. **Live partners** at scale, and partners that can negotiate a new plan.
4. **Other model families**, to see whether these patterns are specific to Claude.
5. **Larger stakes and milder trade-offs**, to map where honesty and commitment give way.

## 8. Materials

The game, prompts, preregistrations, analysis code, episode logs, judgements and a replay viewer that steps through any episode are in the project repository, available from the author. Each round is tagged (`round-1` to `round-8`), and each preregistration records when it was approved relative to the first episode.

## About the author and the Lab

Dr. Mark Ollila wrote this paper at the Endless Games and Learning Lab, a research and innovation lab at Arizona State University in the Herberger Institute for Design and the Arts, based at the MIX Center in Mesa. The Lab's mission is infinitely scalable and affordable learning through games.

**Tools.** The study's code was written with Claude Code and reviewed by the author. The models studied are made by Anthropic, which had no role in the design, analysis or writing.
