# ADR 004 — Scoring v2: 科学的根拠に基づく非補償スコア

**Date**: 2026-06-28
**Status**: Accepted（option (a) "balanced" を既定として実装）
**Supersedes (部分的)**: 002 / 003 の線形 z スコア和（v1。UI には残置）

---

## Context — なぜ v1 を作り直すか

v1 のスコアは z スコアの線形重み付き和（全項重み1、`compute_overall_scores`）。
多角リサーチ + 敵対的検証（3ラウンド）と全156地点での実測により、目的（双極性障害・うつ症状を持つ配偶者の永住地選び。仮説「光の量より変化率（デルタ）が重要」）に対して次の弱点が確認された。

1. **サンプル依存**: z は対象156地点の平均/分散基準。地点を足し引きすると全順位が動く＝絶対基準がない。
2. **補償型**: floor が最悪でも他軸で挽回できる。例: 根室は winter_floor が弱い（百分位71）が変動が極小なので v1 では上位。ユーザーが望むのは「floor が一定以上 **かつ** delta が小さい」という**非補償**。
3. **手選び4遷移指標の冗長 / 秋以外の delta 未捕捉**: spring/autumn の gain・drop・rise・fall_days は単峰掃引の速さの別表現。一方、梅雨の踊り場のような**非季節的な往復**はどの指標も捕捉していない。

## 科学的根拠（一次文献、実在確認済み）

| 軸 | エビデンス | 設計への含意 |
|---|---|---|
| **変化率（春の立ち上がり勾配）** | 患者アウトカム最強。Bauer 多施設（n=5,536/32カ国）: 春季の月間日射**増加率**が大きい土地ほど双極I型の発症が早い（最大↔最小で約5年差, P<0.001）。*Acta Psychiatr Scand* 2017, PMID 28722128 / *J Affect Disord* 2014, PMID 24953482 | `spring_30d_gain` を **Tier-1 ペナルティ**に。仮説「量より変化率」の本体。 |
| **冬季の光レベル** | 機構的に最厚（SAD病型・光療法・Wehr 2001 PMID 11735838）。ただし生理的応答は**飽和**する（Zeitzer 2000 PMID 10922269: ~100lux半最大・~1000lux飽和、光療法 5000lux·h でプラトー）。集団リスクが下限で**不連続にジャンプする閾値の証拠は弱い**。 | `winter_floor` は **凹型・飽和ペナルティ**で（ハード足切りゲートは**不採用**）。 |
| 双方向性 | 双極性では低い冬光（うつ）と急峻な春増加（躁・早期発症）の**両方**が悪。「光最大化」は誤り。 | `autumn_30d_drop`（うつ側）も Tier-1b に。ただし患者アウトカム実証が強いのは春側ゆえ **w_spring ≥ w_autumn**。 |
| 短期変動/年較差/年平均 | 直接エビデンス乏しい。 | `excess_tv`・`amplitude` は **Tier-2（低ウェイト探索変数）**。 |
| 緯度↔SAD有病率 | 弱い正の勾配（Kim 2025）/ 北米外で非有意（Mersch 1999, PMID 10363665）。 | **収束チェックのみ**。主軸にしない。 |

補足: 「rate of change is a hidden variable」論文は実在するが著者は **Sandra J. Rosenthal（化学者）**で、SAD の Norman Rosenthal ではない（*Brain Behav* 2021, PMID 34061463）。実証本体は Bauer。

## 構成概念妥当性の限界（正直なラベル）

- 30年**平年値**を 15日移動平均した系列の変動は、**総観スケール（連続曇天）・年々変動を消去**する。
  → 現 `total_variation` は「実生活の光ボラティリティ」ではなく**「季節傾き + 梅雨構造」**。短期変動を本当に測るには日別生データ（1991–2020）が要る。
- 地点別の発症率という outcome が無い → **構成概念妥当性**しか担保できない（各指標のリスク符号を事前宣言し、既知群＝日本海側が最悪・道東が良、で点検済み）。
- 居住可能性フィルタ未適用（南鳥島・父島など無人島が上位に出る）。

---

## Decision — v2 スコア定式化

データ衛生 → 絶対アンカー desirability → 非補償集約、の3段。`analysis/compute_metrics.py` 実装。

**Stage 0 — データ衛生**: `latitude is None`（昭和基地＝南極、北半球の季節ロジックが破綻）と 365日未満（富士山）を除外。

**Stage 1 — アンカー付き desirability d_j ∈ [0,1]**（1=理想、`z` をやめる）。
アンカーは各指標の**全クリーン地点** 5–95 パーセンタイルで**生成時に固定**し `data.json` に焼き込む。ソース別（日射 MJ / 日照 h で単位が違う）。
注: これは「生成時固定」であって「物理的絶対」ではない（アンカーは地点集合から導出される）。アプリ内では固定なので、重みを動かしても行を隠してもアンカーは動かない＝z スコアに無い性質。ただし**地点集合そのものが変われば動く**。よってアンカーは必ず**全クリーン集合**から計算し、居住可能性フィルタ等は「ランキングから行を落とす」だけにして**再アンカーさせない**こと。完全な不変が要るなら固定アンカー定数を checked-in する（今後の硬化案）。

```
nF = clip((winter_floor − f_lo)/(f_hi − f_lo), 0, 1)
d_floor     = (1 − e^(−k·nF)) / (1 − e^(−k))          # 凹型飽和（暗い冬の差を重視）
d_spring    = 1 − clip((spring_30d_gain − s_lo)/(s_hi − s_lo))   # 緩やかな春＝良
d_autumn    = 1 − clip((autumn_30d_drop − a_lo)/(a_hi − a_lo))   # 緩やかな秋＝良
d_excessTV  = 1 − clip((excess_tv − e_lo)/(e_hi − e_lo))         # 梅雨の踊り場が少ない＝良
d_amplitude = 1 − clip((amplitude − m_lo)/(m_hi − m_lo))         # 季節振幅が小＝良
```

**Stage 2 — augmented weighted Chebyshev（理想点への距離、低い＝良）**:

```
score_v2 = max_j [ w_j·(1 − d_j) ]  +  ρ·Σ_j w_j·(1 − d_j)
```

- `max` 項 ＝ **弱い軸が支配** ＝ 非補償（「floor 十分 **かつ** delta 小」を要求、ただし崖なし）。
- `ρ` 項 ＝ 同点を全体均衡で破る微小線形（weak-Pareto を避け真の Pareto 最適のみ選ぶ標準手法）。

**既定重み (a) "balanced"**:
`w_floor=1.0, w_spring=1.0, w_autumn=0.7, w_excessTV=0.3, w_amplitude=0.3, k_floor=2.5, ρ=0.1`

**Stage 3 — Pareto 前線を一次の意思決定メニューに**: `winter_floor↑ × total_variation↓` の非劣解を `pareto` フラグで提示。スカラー（score_v2）は二次。

### (a)/(b)/(c) は同一式の重み違い

- **(a) balanced**（既定）: Tier-1 等重み。Pareto 前線から家族が選ぶ。
- **(b) floor 優先**: `w_floor` を上げる → 北関東（熊谷/前橋/宇都宮）・東海の明るい冬が浮上。
- **(c) delta 優先**: `w_spring/w_autumn/w_excessTV` を上げる → 道東の穏やかな掃引が浮上。

→ **(b)(c) は別実装ではなく、web UI の重みスライダーを動かすだけ**。v2 のパラメータ（重み5本 + k + ρ）と絶対アンカーを `data.json` に出力し、UI がクライアント側で score_v2 を再計算・再ランクする。

## Consequences

- **既定 (a) の実測**（日照, クリーン後155地点）: 上位＝帯広/広尾/根室/釧路（道東太平洋側）。Pareto 前線(8)＝網走・帯広・釧路・根室・広尾・宇都宮・前橋・熊谷。沖縄（曇り冬で floor 低）と日本海側（両軸最下位）は沈む＝「赤道直下がお得」は不成立。
- v1（線形 z 和）は UI に**残置**（比較・教育用）。`data.json` は v1・v2 両方のフィールドを持つ。
- `data.json` の生成を `compute_metrics.py:export_web_json` に組み込み**再現可能**化（従来は生成スクリプトが repo に無かった）。
- 重み・アンカー・k・ρ・ゲート無しは**判断レバー**。感度分析（重み ±50%、アンカー、k/ρ で上位の順位安定性）は `analysis/sensitivity_v2.py`。
- **居住可能性フィルタ**: 南鳥島（s47991, 民間定住なし＝自衛隊/海保のみ）のみ除外。**アンカー計算の後**に適用するのでアンカーは動かない（全クリーン集合基準のまま）。有人離島（父島・南大東など）は残置。全指標カタログ（station_metrics_*.csv）は全地点保持。
- 日別生データによる真のボラティリティ指標は未実装（次フェーズ）。

## Windowing（固定窓の扱い）

- **変動・梅雨系（excess_tv / total_variation）は固定窓を使わない**。年周全体の Σ|Δ| ベースなので、梅雨の時期が地域でバラバラ（沖縄は5–6月・東北は7月・北海道はほぼ無い）でも、**起きた時期に関係なく**捕捉する。固定の「6–7月」窓で梅雨を測る素朴案の欠点（緯度で時期がずれて無意味になる）を回避している。
- 一方 **`winter_floor`（12/15–2/15）と `summer_ceiling`（6/15–8/15）は固定窓**。冬至・夏至は天文的にほぼ全国共通なので妥当だが、厳密には日の出入りの地域差を無視している（既知の簡略化）。気になれば各地点の太陽幾何（日長）基準に置換するのが今後の精緻化。
- 春/秋レート（spring_30d_gain / autumn_30d_drop）は固定窓ではなく**各地点のトラフ→ピーク／ピーク→トラフ区間**から算出するので、季節進行の地域差に追随する。

## References（基準とした文献）

リサーチ + 敵対的検証で**実在を確認**できたもの。確信度は本文の表を参照。

**変化率（rate of change）— v2 の春/秋レート項の根拠**
- Bauer M, et al. "Solar insolation in springtime influences the age of onset of bipolar I disorder." *Acta Psychiatr Scand.* 2017;136(6):571–582. PMID 28722128. <https://pubmed.ncbi.nlm.nih.gov/28722128/> — 多施設国際研究（n=5,536）。春季の月間日射「増加量(rate)」が大きい土地ほど双極I型の発症が早い。**最良の患者アウトカム証拠**。
- Bauer M, et al. *J Affect Disord.* 2014（国際多施設, 日射と発症）PMID 24953482. <https://pubmed.ncbi.nlm.nih.gov/24953482/>
- Rosenthal SJ, Josephs T, Kovtun O, McCarty R. "Rate of change in solar insolation is a hidden variable that influences seasonal alterations in bipolar disorder." *Brain Behav.* 2021;11(7):e02198. PMID 34061463. <https://pmc.ncbi.nlm.nih.gov/articles/PMC8323043/> — 理論/再解析論文。**著者は化学者 Sandra J. Rosenthal で、SAD の Norman Rosenthal ではない**（帰属注意）。

**冬季の光レベルと飽和 — winter_floor を「ハード閾値」でなく「凹型飽和」にする根拠**
- Zeitzer JM, et al. "Sensitivity of the human circadian pacemaker to nocturnal light: melatonin phase resetting and suppression." *J Physiol.* 2000;526(Pt 3):695–702. PMID 10922269. <https://pmc.ncbi.nlm.nih.gov/articles/PMC2270041/> — 概日応答は ~100lux で半最大・~1000lux で飽和（シグモイド）。閾値的「崖」ではなく飽和。
- Wehr TA, et al. "A circadian signal of change of season in patients with seasonal affective disorder." *Arch Gen Psychiatry.* 2001;58(12):1108–1114. PMID 11735838. <https://pubmed.ncbi.nlm.nih.gov/11735838/> — 冬の夜間メラトニン延長＝季節シグナル。

**緯度勾配 / SAD有病率 — 収束チェックのみ（主軸にしない）**
- Mersch PPA, et al. *J Affect Disord.* 1999;53(1):35–48. PMID 10363665. <https://pubmed.ncbi.nlm.nih.gov/10363665/> — 緯度の影響は限定的（北米外で非有意）。
- Kim K, et al. "Global prevalence of seasonal affective disorder by latitude: systematic review and meta-analysis." *J Affect Disord.* 2025. doi:10.1016/j.jad.2025.119807 — 冬型SADで弱い正の緯度勾配。

> 注: 個人 outcome（地点別発症率）が無いため、これらは**構成概念/収束的妥当性**の根拠であって因果の証明ではない。各指標のリスク符号は事前宣言し、既知群（日本海側＝最悪・道東＝良）で点検済み。

## 関連メモ
`memory/scoring-redesign-science.md`, `memory/autumn-vs-spring-symmetry.md`
