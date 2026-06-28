# ADR 008 — スコア v3（実効昼光 sufficiency + 秋レート + 急性連続曇天）

**Date**: 2026-06-28
**Status**: Accepted（v2 を温存しつつ、v3 を UI に追加。既定モデルは当面 v2、v3 は実効光モデルとして選択可）。**位置づけ: v3 は「気候除外スクリーン＋候補ドシエ生成器」であって、永住地の最終スカラー順位ではない**（cross-review breaker の指摘を反映、後述）。
**関連**: [004 v2スコア](004_phase2-scoring-v2.md) / [005 GHI推計の却下](005_ghi-estimation-rejected.md) / [006 冬の光環境スクリーニング](006_winter-light-screen-conclusions.md) / [007 GISワークベンチ](007_gis-workbench.md)

---

## Context

v2（[004]）は **冬 floor + 季節遷移レート + 安定性** の非補償モデル。運用して 2 つの問題が出た。

1. **絶対的な明るさ（全天日射量 MJ/m²）が事実上スコアに効いていない。** v2 の floor は源ごとの 5/95 percentile で正規化するため、源内の相対位置しか見ない。結果、**道東モノカルチャー**（v2 top-10 に 帯広・広尾・根室・釧路・網走… が 7 地点）。これは「光環境が良い」のではなく、Codex consult の指摘どおり「**夏も暗いので年較差が小さい**」地点が amplitude 軸で得をしていた（amplitude の desirability が夏の暗さで上がる mis-specification）。
2. **配偶者の臨床アンカーに反する。** 本人は **札幌で著明に悪化**したが、v2 は**日照時間源で 札幌 #26 > 東京 #28** とこれを破っていた（GHI源では満たす）。これが「**GHI が日照時間より気分プロキシとして良い**」最初の具体証拠（[clinical-anchors メモ]）。

本人の lived な光→気分パターン（fit ではなく **validation** 用）:
- **秋（9–10月）→年末にかけてドロップ、冬至前後で回復**（光の減少が止まる微分→0 のタイミングで上向く。最も暗い1–2月ではない）。→ うつ極は**秋の下降レート（delta）**に追従。「変化率 > 量」仮説の復活（ただし秋のドロップに限定）。
- **春の賦活は重要でない** → spring を de-weight（パラメータで可変）。
- **数日連続の無日照で数日内に悪化**（梅雨）。世田谷 2026-06-25/26/27 = 0.0h×3 と本人の低調が一致。冬 floor では捕捉できない**急性・非冬期の軸**。

## Decision

**v3 =「実効昼光 sufficiency + 冬低光量 red-flag + 秋下降レート + 急性連続曇天」**。v2 を捨てず、UI に第3モデルとして追加（`web/index.html` の v2/v3/v1 トグル）。

### 1. 実効昼光 E(d)（幾何補正）— v3 の主軸 `winter_floor_eff`
気象庁の屋外観測を**太陽高度・昼長で補正**して、retina に近い「日中の実効エネルギー」に寄せる。
- **日照時間源**: `E = clip(n/N, 0, 1) · Ra`（n=日照時間, N=可照時間, Ra=大気外日射, いずれも FAO-56 で緯度+DOYから決定論的）。**a=0, b=1 の固定**で、Ångström–Prescott の **係数を当てはめない** → [005] が却下した「高地外挿で未検証の a,b フィット」を**回避**。各「明るい1時間」をその緯度・季節の clear-sky エネルギーで重み付けするだけ。
- **GHI源**: `E = 実測 MJ`（既に幾何を内包）。
- `winter_floor_eff = mean(E over 12/15–2/15)`。**k_floor=0（線形）が既定**（暗い floor を甘く見ない）。

これにより **札幌 < 東京 が構造的に成立**（高緯度は冬の太陽が低く昼も短い＝実効光が物理的に小さい）。合成データの単調性テストでも `4h一定: lat43 eff=5.91 < lat35 eff=7.34`、`Ra(12/22) 43N=11.63 < 35N=16.20`。

### 2. 秋/春レート — 固定天文窓 × 相対化
`autumn_rate = (E の秋窓内 30日最大下落) / (E の季節レンジ P95−P05)`。
- **窓は固定の天文窓**（秋分→冬至 / 冬至→春分）。データ駆動の peak→trough は梅雨の谷（例 東京 DOY≈172）に秋を**誤配置**するため不採用。
- **相対化**（/季節レンジ）は、Codex breaker の懸念「dim-summer 地点は落ちしろが小さく**見かけ上 gentle な秋**で不当に得をする」への対処。
- 既定 `w_autumn=0.7`（うつ極に追従するためやや重い）、`w_spring=0.3`（春賦活は軽い）。

### 3. 急性連続曇天 `acute_spell`（独立軸, 実日別）
非冬期（4–10月）の「日照<2h が連続した最長日数」の **CVaR80**（worst 20% 年の平均）。冬 dark-spell（[006]）と**季節非重複**なので winter_floor と二重計上しない（梅雨の live 検証軸）。既定 `w_acute=0.2`。

### 4. amplitude を除去、夏は片側キャップのみ
`w_amplitude=0`（v2 から外す。reversible lever としてスライダーは残す）。年間総量の無制限報酬は入れず、躁転対策の**一方向 summer cap** を `w_summerCap`（既定0）でオプション化。

### 5. 冬 dark-spell は red-flag GATE（重み0, スコア項にしない）
冬リスク band が **赤**の地点は `d_floor` を `gate_cap=0.4` で頭打ち（数週間の連続曇天を gentle-transition 軸で買い戻せない非補償ゲート）。winter_floor と r≈−0.88 で重複するため**項にはせず**、ゲートとしてのみ使う（[006] の方針）。

### v3 既定重み
`w_floor=1.0, w_autumn=0.7, w_spring=0.3, w_excessTV=0.3, w_acute=0.2, w_amplitude=0.0, w_summerCap=0.0, k_floor=0.0, gate_cap=0.4, rho=0.1`。集約は v2 と同じ **augmented weighted Chebyshev**（最弱軸支配 + ρ で同点割り）。**全てUIスライダーで可変**。

## 確証バイアス回避（事前登録プロトコル）

consult の助言どおり、**重みではなく判定プロトコルを先に固定**。本人の lived アンカーは **validation 標的であって fit 標的にしない**。`analysis/validate_v3.py` が凍結モデルに対して以下を実行（チューニングは一切しない）。

## 検証結果（`analysis/validate_v3.py`、両源）

**事前登録テスト: ALL PASS。**

| テスト | 結果 |
|---|---|
| 幾何単調性（合成データ・物理サニティ） | PASS（高緯度の実効冬光 < 低緯度） |
| **札幌 < 東京** ※下記*1 | PASS（日照: 東京#27 > 札幌#120 / GHI: 東京#4 > 札幌#38） |
| モノカルチャー解消（top10の道北/道東 ≤1） | PASS（v2=7 → **v3=0**） |
| anti-overfit（候補が#1独占でない） | PASS（日照 top3 = 潮岬/伊良湖/飯田、**松本#11**） |
| 道東の降格（v2→v3, 日照） | 帯広 #1→**#41**、釧路 #5→#79、根室 #3→#77、網走 #10→#104 |

**(a) 重みグリッド・ロバスト性 — 教義の周り（N=4000, 既定範囲, 日照源）** — 候補が「特定重みでだけ上がる」かを検査:
- **robust top group（本物）**: 飯田 top-decile 80%・中央#9、伊良湖 84%・#7、諏訪 56%・#14、松本 43%・#17。
- **weight-sensitive（正直な留保）**: **軽井沢 25%・中央#42、甲府 13%・中央#36**（best は top-3 だが中央は中位＝特定重みでだけ上がる）。**v3 は軽井沢/甲府を robust には推さない**。本人が挙げた候補のうち 2 つを「条件付き」と判定＝検出器が機能（全候補を持ち上げる rig になっていない）。移住検討上の実用的所見でもある。

**(b) 全UI重み空間でのロバスト性（N=4000, floor→0 / acute→3 / 赤旗ゲート無効も許す）** — breaker の指摘「(a) は『自分の教義の周り』のロバスト性に過ぎない」への対処。利用者が UI で作れる**最悪ケース**:
- robust性は当然弱まる: 飯田 80%→**57%**、伊良湖 84%→36%、諏訪 56%→31%、松本 43%→34%。
- **極端重みでは札幌すら top-decile に入りうる（全UI top10%=18%）**（floor を 0 にして acute/spring を最大化する等）。**隠さず開示**。
- → だから v3 は**「単一スカラー順位」ではなく「除外スクリーン＋候補ドシエ」**として読む。教義（floor 主軸）の周りでは robust、教義を捨てれば順位は壊れる。

**(c) アンカー感度（5/95→1/99/2.5/97.5/10/90, 日照源）** — breaker の「候補の序列はアンカー依存」への対処:
- **除外は anchor 不変で安定**: 札幌 #119–120、東京 #21–31。
- **上位の序列は動く**: 1/99 で 諏訪 #4→#16、松本 #11→#23、飯田 #3→#7。→ 上位は**「正確な序列」でなく「robust な上位群」**として読む（(b) と同じ結論）。

**(d) k_floor × gate_cap 感度（24セル, 両源）**: 札幌<東京 と モノカルチャー解消が **全セルで 100%** 成立（単一セルの artifact ではない）。

**(e) 緯度交絡コントロール + 増分価値（正直開示）**:
- `corr(score, lat) = +0.33`（高緯度ほど悪い、という傾き。隠さない）。**R²=0.11 → 緯度だけで説明できるスコア分散は 11% のみ、残り 89% は雲/局地/季節構造**（「ただの緯度ランカー」を定量的に反証）。
- `corr(winter_floor_eff, lat) = −0.34` vs `corr(raw winter_floor, lat) = −0.14`。**幾何補正で緯度相関が強まる** = 高緯度の冬の実効光不足という**物理を回復**（捏造 prior ではない。GHI 実測自身の floor-緯度相関が強いことと整合）。
- **同緯度対照**: 内陸盆地候補の score 残差（緯度回帰残差, 負=緯度予測より良い）松本 −0.39 / 諏訪 −0.43 / 飯田 −0.42 vs ほぼ同緯度の日本海側ピア 金沢 +0.32 / 富山 +0.27 / 新潟 +0.34。**金沢#136 vs 松本#11（緯度ほぼ同一）**。
- **「雲のみ」成分**（winter_floor_eff を緯度回帰した残差での順位）でも 松本#33 / 諏訪#23 / 飯田#31 が上位、金沢#132 / 新潟#140 が下位。→ **緯度を完全に除いても**、日本海側の冬の雲という実在の光環境差が残る（breaker の「latitude prior with cloud residuals」へ: 雲残差それ自体が候補をピアより上に並べる）。

**(f) autumn_rate 相対化のゲーミング検査**: `corr(autumn_rate, summer_ceiling) = −0.02`（日照）/ `+0.11`（GHI）＝**ほぼゼロ**。breaker の懸念（相対化が dim-summer を不当優遇）は**実証的に否定**。ただし `corr(autumn_rate, amplitude) = +0.37`（日照）/ `+0.42`（GHI）＝**分母（季節レンジ）経由で年較差を中程度混入**している（下記*2 の留保）。

## 正直な留保（隠さない）

- ***1 「札幌<東京」は独立した反証テストではない。** 日照源では winter_floor_eff が幾何補正により高緯度を**構造的に**罰するので、これは**仕様適合（spec-conformance）**であって独立検証ではない。**GHI源（実測 MJ, 幾何 tilt なし）が弱い独立チェック**で、そこでも 東京#4 > 札幌#38 が成立。つまり「設計制約」と「検証」を兼ねさせない（cross-review breaker の指摘）。本人の lived アンカーのうち独立検証に使えるのは GHI 源側のみ。
- ***2 autumn_rate は年較差を中程度混入する。** `corr(autumn_rate, amplitude) ≈ +0.37–0.42`。amplitude を w=0 にしても、autumn_rate（w=0.7）の分母（季節レンジ）経由で年較差が**弱く残存**する（純粋な「秋の下降の急さ」ではない）。相対化は dim-summer 報酬は消したが、構造的に annual amplitude と完全には分離していない。スクリーンとしては許容、絶対値の `autumn_30d_drop` も UI に併置して可視化。
- **候補の上位序列は重み・アンカーに敏感（(b)(c)）。** 除外（道東/札幌の降格）は robust だが、top の順位は doctrine とアンカーに依存。**v3 は「正確な永住地ランキング」ではなく「明らかに悪い気候を外す除外スクリーン＋候補ドシエ生成器」**。最終判断は臨床・生活・hard-filter の第2層が要る（breaker bottom line）。
- **December-trough テスト 61%**（目標95%未達）。E(d) の年間最小が 12月でない地点が約4割（梅雨/海霧で夏に底）。だが **autumn_rate は固定天文窓で測る**ので全体トラフがどこでも秋の下降は捕捉される。旧「データ駆動トラフ」案向けの指標で、固定窓採用により**実質 moot**（informational のみ）。
- **acute_spell（既定 w=0.2）は top-10 を動かさない**（leave-one-axis-out で acute drop = 変化なし）。上位地点は既に acute が低いため。中位の弁別 + 梅雨の live 検証軸としては機能（w を上げると帯広等が大きく下がる）。
- **v3 の新規性の正直な枠組み**: 革新ではなく **(a) 秋窓の修正（固定天文窓 + 相対化）, (b) 幾何補正 floor で 札幌<東京 を物理的に担保, (c) 季節非重複の acute_spell 追加, (d) amplitude 除去で dim-summer 報酬を排除**。「松本/諏訪を上げるための後付け」ではなく、根室/釧路が『光環境が良い』と誤評価される**仕様バグの修正**。
- **屋外気象 ≠ 網膜光**。住居の窓向き・在宅時間・夜間光・朝散歩の方が地理差より効きうる。これは**気候スクリーニングであって臨床判断ではない**（主治医に委ねる）。
- **構造的に未実装（breaker, 将来課題）**: held-out 臨床検証（本人の年次エピソードとの突合）、室内/光行動要因、睡眠相・経度、医療/生活の hard-filter、夏の hypomania リスク、盆地の局地微気候、**候補盆地の高地 GHI 実測検証**（[005] の外挿問題）。本 ADR の範囲外で、第2層として別途。

## cross-review（builder + breaker）の要約

`cross-review`（CLAUDE.md 必須）を実施。**builder（review agent）= コード欠陥なし**: JS `v3Desir/v3Score` が Python `v3_desirabilities/v3_score` を**ビット一致で再現**（赤旗ゲート・acute=null 中立path 含む, `max|JS−score_v3|=0`）、窓境界・NaN ガード・acute join・アンカー refresh すべて検証。`validate_v3.py` は確証バイアス的に clean（本人の候補 軽井沢/甲府 を narrow-win と自ら flag）。

**breaker（Codex, consult fallback。`adversarial-review` helper は失敗）= 構成概念妥当性を指摘**。対応:
- 「札幌<東京 は構造的」→ spec-conformance と再分類（*1）。**[対応済]**
- 「acute アンカーが source subset を漏らす」（日照154点 vs GHI47点で別アンカー）→ **source 不変の global acute アンカーに修正**（`compute_global_acute_anchor`）。**[対応済]**
- 「robustness が doctrine ローカル」→ **全UI重み空間の adversarial grid を追加**（(b)）。**[対応済]**
- 「ただの緯度ランカーでは」→ **R²=0.11 + 雲のみ成分順位を追加**（(e)）。**[対応済]**
- 「候補序列がアンカー依存」→ **アンカー感度テストを追加**（(c)）。**[対応済]**
- 「autumn_rate の分母が amplitude を混入」→ 検査を拡張し `+0.37` を**正直開示**（*2）。**[対応済]**
- 「スカラー順位として強すぎる」→ **位置づけを除外スクリーン＋ドシエに再framing**（Status / 留保）。**[対応済]**

builder（実装は主張どおり）と breaker（主張が強すぎ）は矛盾ではなく**層が違う**: 正しい実装の上で headline の主張を絞った。

## 実装

- `analysis/compute_metrics.py`: FAO-56 幾何（`_ra_n`, `_eff_series`）、固定天文窓（`_AUTUMN_WIN`/`_SPRING_WIN`）、`Metrics` に v3 フィールド、`compute_metrics(values, lat, energy)`、v3 スコア（`V3_DEFAULTS`/`compute_v3_anchors`/`v3_desirabilities`/`v3_score`）、**source 不変の `compute_global_acute_anchor`**。`export_web_json` が各行に `d3_*`・`score_v3`、payload に `v3_anchors`/`v3_defaults`/`v3_objectives`。
- `analysis/acute_spell_all.py`: 非冬期 acute dark-spell CVaR80（実日別キャッシュから）→ `data/acute_spell_metrics.csv`（派生のみ commit）。
- `web/index.html`: v3 トグル + 10 スライダー + JS スコア（`v3Desir`/`v3Score`、Python と完全一致。max |JS−Python| = 5e-5）+ Compare 表/レーダー/地図配色に v3 指標。
- `analysis/validate_v3.py`: 本 ADR の検証スイート（§0 幾何単調性 / §1 事前登録 / §2 教義ロバスト / §2b 全UIロバスト / §2c アンカー感度 / §3 leave-one-axis-out / §4 k×gate / §5 緯度コントロール / §5b 緯度増分 / §6 autumn ゲーミング）。幾何は `compute_metrics` を単一ソースとして import。

## Consequences

- 道東モノカルチャーが物理的理由で解消、札幌<東京 が構造的に担保、候補は earned（松本/諏訪/飯田 robust、軽井沢/甲府 は条件付きと正直開示）。
- 生の日別観測（`data/jma_daily/`）・NEDO は [005]/[006] どおり gitignore のまま、派生指標のみ commit。
- 既定モデルを v3 に切り替えるかは**別判断**（本 ADR では v3 を追加・選択可にするに留め、UI 既定は v2）。
