# Explainable Hotspot Severity Scoring Specification

Repo: `ShreyaJ-27/omni-grid-civic-issue-triage`  
Component: `core.omnigrid.cluster.compute_severity_score`  
Track 2: **O3: Trustworthy and Verifiable AI**

---

## 1. Overview & Trust Philosophy

In line with Omni-Grid's core tenet—**"Numbers from code only"**—hotspot severity is strictly deterministic, fully explainable, and reproducible. The Large Language Model (LLM) or Vision-Language Model (VLM) never hallucinates composite priority scores; rather, the triage pipeline evaluates verifiable facts, spatial density, temporal decay, defect severity, and safety signals using an explicit mathematical formulation.

---

## 2. Mathematical Formulation

The composite severity score $S \in [0.0, 10.0]$ is computed as:

$$\text{severity} = 10.0 \times \left( 0.45 \times S_{\text{count}} + 0.30 \times S_{\text{vlm}} + 0.15 \times S_{\text{recency}} + 0.10 \times S_{\text{injury}} \right)$$

where each sub-score $S_i \in [0.0, 1.0]$. The final composite score is clamped to $[0.0, 10.0]$ and rounded to two decimal places.

### Component Summary

| Component | Weight | Mathematical Definition | Bounds | Description |
|---|---|---|---|---|
| **$S_{\text{count}}$** (Density) | **45%** ($0.45$) | $\min\left(1.0, \frac{\log_2(1 + n)}{\log_2(1 + 20)}\right)$ | $[0.0, 1.0]$ | Logarithmic scaling with diminishing returns, saturating at $n = 20$ complaints. |
| **$S_{\text{vlm}}$** (Visual Damage) | **30%** ($0.30$) | $\frac{1}{\|V\|} \sum_{v \in V} v$ | $[0.0, 1.0]$ | Mean visual damage score from verified VLM metadata. **Fallback to $0.0$ if missing.** |
| **$S_{\text{recency}}$** (Temporal Decay) | **15%** ($0.15$) | $\exp\left(-\ln(2) \cdot \frac{\text{age\_hours}}{72.0}\right)$ | $[0.0, 1.0]$ | Continuous exponential decay with a 72-hour half-life based on the newest complaint. |
| **$S_{\text{injury}}$** (Safety Keywords) | **10%** ($0.10$) | $\frac{\text{count}(\text{safety matches})}{n}$ | $[0.0, 1.0]$ | Fraction of tickets mentioning injury/hazard keywords. |

---

## 3. Detailed Component Mechanics

### 3.1. Complaint Count Score ($S_{\text{count}}$)
Civic issues aggregate quickly in dense corridors. Linear scaling would cause large clusters to dominate without differentiation. We use a base-2 logarithmic curve:

$$S_{\text{count}} = \min\left(1.0, \frac{\log_2(1 + n)}{\log_2(21)}\right)$$

- $n = 0 \implies S_{\text{count}} = 0.0$
- $n = 1 \implies S_{\text{count}} \approx 0.228$
- $n = 5 \implies S_{\text{count}} \approx 0.589$
- $n = 10 \implies S_{\text{count}} \approx 0.788$
- $n = 20 \implies S_{\text{count}} = 1.000$ (saturation threshold)
- $n > 20 \implies S_{\text{count}} = 1.000$ (capped)

### 3.2. VLM Visual Damage Score ($S_{\text{vlm}}$) & Explicit Fallback
Vision-Language Models assess image evidence on a continuous scale from $0.0$ (cosmetic/negligible) to $1.0$ (catastrophic road collapse or downed high-voltage line).

- When visual evidence has been scored:
  $$S_{\text{vlm}} = \frac{1}{|V|} \sum_{v \in V} v$$
  where $V$ is the set of valid visual damage scores in the cluster.

> [!IMPORTANT]
> **Explicit Fallback Mechanism**:
> If vision damage scores are unavailable, missing, or if tickets lack media attachments, $S_{\text{vlm}}$ **strictly falls back to `0.0`**. The system **never** invents, extrapolates, or silently hallucinates an assumed damage score. This guarantees that uninspected tickets do not artificially inflate public safety priority without verifiable visual proof.

### 3.3. Recency Score ($S_{\text{recency}}$)
Urgency decays smoothly over time unless refreshed by new tickets. The score evaluates the newest complaint in the cluster relative to a reference time $T_{\text{ref}}$ (defaulting to current UTC):

$$\text{age\_hours} = \frac{T_{\text{ref}} - \max(t_{\text{created\_at}})}{3600}$$

$$S_{\text{recency}} = \exp\left( -\frac{\ln 2}{72.0} \times \max(0.0, \text{age\_hours}) \right)$$

- If the newest complaint is brand new ($\text{age\_hours} \le 0$): $S_{\text{recency}} = 1.0$.
- After 72 hours (3 days) with no new reports: $S_{\text{recency}} = 0.5$.
- After 144 hours (6 days): $S_{\text{recency}} = 0.25$.
- After 216 hours (9 days): $S_{\text{recency}} = 0.125$.

### 3.4. Safety / Injury Signal Score ($S_{\text{injury}}$)
Citizen complaint narratives are searched for critical safety cues using case-insensitive keyword boundaries:
- `injury` (e.g., "injuries", "injured")
- `accident` (e.g., "accidents", "accidental")
- `fallen` (e.g., "fallen tree", "fallen pole")
- `hospital` (e.g., "hospitalized", "hospital")
- `hazard` (e.g., "hazardous", "hazard")
- `critical` (e.g., "critical situation")

$$S_{\text{injury}} = \frac{\sum_{i=1}^n \mathbf{1}_{[\text{ticket } i \text{ contains safety keyword}]}}{n}$$

If $100\%$ of tickets in a cluster mention a safety risk, $S_{\text{injury}} = 1.0$.

---

## 4. Bounding and Precision

1. Each sub-score is clamped to $[0.0, 1.0]$.
2. The linear sum is multiplied by $10.0$, yielding theoretical range $[0.0, 10.0]$.
3. Clamped via $\max(0.0, \min(10.0, \text{score}))$.
4. Rounded to 2 decimal places (e.g., `7.42`).

---

## 5. Reference Verification Example

Given a cluster of $n = 14$ complaints:
- $S_{\text{count}} = \frac{\log_2(15)}{\log_2(21)} = \frac{3.90689}{4.39232} \approx 0.8895$
- Missing VLM scores $\implies S_{\text{vlm}} = 0.0$ (fallback)
- Newest complaint 12 hours old $\implies S_{\text{recency}} = 2^{-12/72} = 2^{-1/6} \approx 0.8909$
- 3 out of 14 tickets mention "hazard" or "accident" $\implies S_{\text{injury}} = \frac{3}{14} \approx 0.2143$

Calculation:
$$\text{severity} = 10.0 \times \left( 0.45 \times 0.8895 + 0.30 \times 0.0 + 0.15 \times 0.8909 + 0.10 \times 0.2143 \right)$$
$$= 10.0 \times \left( 0.4003 + 0.0 + 0.1336 + 0.0214 \right) = 10.0 \times 0.5553 = 5.55$$
Result: `5.55`
