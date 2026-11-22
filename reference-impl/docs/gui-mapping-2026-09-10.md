# 動詞 ↔ 畫面 映射表（人用介面的接縫契約）

寫於 2026-09-10。上游：`adr-gui-stack-2026-09-10.md` §9.5 與 §13 的 0b。
機器可讀的那一半在 `gui-contract.json`，版本號在**它自己的 `contractVersion` 欄位**
——這裡不抄。這一行本來抄著 `contractVersion: 1`，而契約已經走到第三版：
**指標不會漂，副本會**（`CLAUDE.md`「Version numbers live in each document's own
header, and nowhere else」）。本檔末尾的變更紀錄是每一版**改了什麼**，不是版本號的第二份。

**這份文件存在的理由，是使用者 2026-09-10 的裁決**：

> 「最大的問題通常是**指令層還不清晰就包上 GUI**，結果還沒正確做好映射導致
> **兩邊要改**。」

所以接縫先寫成白紙黑字，**在任何一行 HTML 之前**。下面每一條「缺」都不是
推測，是 2026-09-10 用三份真實錄音跑出來的——過程與原始輸出在
`first-run-2026-09-10.md`。

---

## 0. 一句話

**四層紀錄早就都在 `_plans/<rev>.json` 裡了**（cue 帶文字與時間、window 帶解碼
指標、stretch 帶語言與一致度、findings/notes 帶 `cue_id` 與字元座標、`script`
帶每一筆提議的刪除）。**代理人介面刻意只回位置，不回內容**（INV-12）。
所以人用面的取文字實作**只有一份**：`ui/read.py` 讀那一個檔，什麼都不重算。

---

## 1. 唯一的讀取來源

    D:/output/TranscriptDesk/校讀/<run>/_plans/<rev>.json
      recording.cues[]      id, start, end, text, window_id, stretch_id
      recording.windows[]   id, start, end, avg_logprob, temperature,
                            compression_ratio, no_speech_prob
      recording.stretches[] id, start, end, language, agreement
      recording.report      vad_kept, vad_enabled,
                            condition_on_previous_text, degenerated
      findings[] / notes[]  id, code, unit, cue_id, char_start, char_end,
                            evidence{}, (reason)
      script[]              op, cue_id, char_start, char_end, text, code, origin
      ops, unverified{}, detectors_skipped[], detectors_failed[]

**接合規則（normative）**：一則 finding 指的字串是
`cues[cue_id].text[char_start:char_end]`。字元座標是**該 cue 自己的**座標，
不是整份稿子的偏移量，也不是 `全文/*.txt` 這個「渲染結果」的偏移量——
`core/editscript.py` 的排序鍵 `(span.cue_id, span.char_start)` 就是這個意思，
而 INV-5 要求座標永遠是來源座標。**人用面不得改用 `.txt` 的偏移量重新定位**：
那是把判決掛到一份渲染上，多一步就多一個會漂的東西。

**不得再重算的東西**（照 `architecture-2026-09-09.md` §1）：windows 不從 cues
重推、stretches 不重切。一版的判決掛在它被量測時的那一版上。

---

## 2. 三個畫面要什麼，今天從哪裡來

| 畫面 | 它要的東西 | 今天的來源 | 狀態 |
|---|---|---|---|
| **讀** 逐句攤開＋標記 | 每句文字與起訖秒數 | 只有 `_plans` 檔 | **G-2**：無動詞 |
| | finding 落在哪一句的哪幾個字 | `_plans` 有 `cue_id`；**回應沒有** | **G-1** |
| | 這一句所在 window 的解碼指標 | 只有 `_plans` 檔（`show` 不給 windows） | G-2 同一條 |
| | 語言段落與一致度 | `show` ✅（`stretches` 帶 agreement） | 夠用 |
| **聽** 點一句播那一秒 | cue 的 `start`/`end` | 只有 `_plans` 檔 | **G-4** |
| | 音訊位元組 | 無；`open` 只是把路徑交給作業系統 | **G-5** |
| **決** 採納／婉拒 | 每一筆提議「要刪掉哪幾個字」 | `_plans.script[]`；`plan` 只回**數量** | **G-3** |
| | 動作的索引（`accept --op N` 用） | `script[]` 的順序 | 契約要釘死 |
| | 未驗證標記 | `plan` ✅（`unverified{}`） | 夠用 |
| | 取消 | `cancel` ✅（帶 `reason_code` 與 `eta_seconds`） | 夠用 |
| | 引用授權 | `cite` ✅（四個必填欄位） | 夠用 |
| **run 清單** | 有哪些 run、到第幾版 | `runs` ✅（`RunRef`） | 見 **G-8** |

### 2.1 第四塊：run 清單上的解碼面板〔2026-09-16，PSM §19 卡片 GUI-4〕

上面那張表寫的是**三個唯讀畫面**。2026-09-16 之後 run 清單頁多了一塊會**讓事情
發生**的面板，這是這張臉的第一條寫入路徑，所以它的控制項與各自打到哪條路由
逐一列在這裡——不是為了好看，是因為一條沒有寫下來的路由就是沒有人審過的路由。

| 控制項 | DOM id | 打哪一條 | 說明 |
|---|---|---|---|
| 錄音下拉 | `source` | `GET /api/intake` | 清單**只有腳本畫**，面板端出的是空的 `<select>`。伺服器端再畫一次就是同一筆紀錄的第二份拷貝 |
| 「Start the decode」 | `go` | `POST /api/decode` | 送出的是**清單自己給過的那個路徑**；白名單在 `src/ltm/ui/sources.py`，是成員判定不是字串檢查 |
| 「Ask it to stop」 | `stop` | `POST /api/cancel` | **送的是請求，不是結果**。狀態變 `cancelling`，變成 `cancelled` 只能由子行程的答覆決定（R13） |
| 「Look again」 | `refresh` | `GET /api/intake` | 重掃。丟進 `--audio-root` 的新檔不必重開伺服器 |
| 進度（`bar`／`state`／`phase`／`counts`） | `live` | `GET /api/progress` | 約一秒一次的輪詢（R13）。**不是 SSE**：`http.server` 沒有事件串流，而單執行緒裡的長連線會擋住讀者馬上要用的那個取消 |

面板自己的狀態寫在 `#decode` 的 `data-state` 上，**只有一個地方寫它**，所以
測試與開著檢視器的人都讀同一個屬性，而不是從「哪個按鈕剛好反灰」去推。

啟動旗標兩個，因為**「頁面能發動解碼」跟「頁面看得到你的磁碟」是兩種權限**：
`--audio-root DIR`（可重複）才讓新錄音構得著，不給就只能重解已經 `ltm intake`
登錄過的；`--read-only` 則把這四條路由全部關掉，回 `503 not-configured`，那是
2026-09-16 之前這張臉的樣子，是一個**正當設定**而不是測試用的開關。

**面板不渲染任何逐字稿文字，而且做不到**（R1）：它顯示的是檔名、階段名、兩個
計數、一個狀態字，跑完之後指向**那個 run 的紀錄**（`/run?run=…`）就停。所以
`/` 仍然不在 `TEXT_ROUTES` 裡——變的是這張臉能讓事情發生，不是它能給字看。
瀏覽器端的實測（開始鍵在跑的時候是反灰的、進度文字在兩次輪詢之間有變、
`cancelling` → `cancelled` 中間隔了一次輪詢）在
`docs/evidence/gui4_dom_run_2026-09-16.md`。

---

## 3. 缺口清單（每一條都有 2026-09-10 的實測證據）

### G-1 · 回應丟掉了 `cue_id`，於是只能解析顯示字串 〔高／要裁決〕〔**2026-09-11 已關**〕

**〔2026-09-16 補：這一節寫的是 2026-09-10 的現況，而 G-1 在 2026-09-11 就關掉了〕**
——見本檔末尾變更紀錄 contractVersion 2，以及 `gui-contract.json` 的
`"status": "closed 2026-09-11"`。今天 `SpanRef` 有**八**欄，`cue_id` 在裡面。
以下原文保留，因為它說的是「為什麼要改」，那部分沒有過期（第十三輪 R13a-S8）。

`_plans` 的 finding 列有 `cue_id: "c1644"`，但 `agentapi/schema.py` 的
`SpanRef` **只有** `id / code / unit / char_start / char_end / evidence / reason`，
`verbs._span_ref()` 讀了那一列卻沒有把 `cue_id` 帶出來。回應裡唯一還看得出位置的
東西是 `id`：`"engine-loop:1644-1649"`。

於是任何一張臉要定位，都得**切一個給人看的字串**。這正是本產品自己在
`architecture-2026-09-09.md` §4.5 反對的形狀（回應型別裡不放自由文字，位置要
結構化）。

**「那就數行數」不是解法。** `全文/*.txt` 是一行一 cue，所以在 `r000` 上
「第 1645 行」確實就是 `c1644`——2026-09-10 實測對得上。但 `render_txt()` 的
註解自己寫著「Line n 是第 n 個**存活的** cue」：一旦 `accept` 刪掉任何一句，
行號與 cue 代號就不再對齊，而**沒有任何東西會告訴讀的人對齊已經斷了**。
一個只在「還沒有人採納過任何提議」時才成立的定位法，正是這個產品最不該有的
那種東西。

**裁決題**：把 `cue_id` 加進 `SpanRef`（動代理人 schema，要走 PIM 增修），
還是把 `id` 的格式升格為契約？前者較乾淨；後者零改動但把顯示字串變成 API。
**我的建議是前者**——`cue_id` 是位置不是內容，加它不觸 INV-12。

### G-2 · 沒有任何動詞回得出文字（**這是設計，不是缺陷**） 〔高／已有解法〕

INV-12 明文：代理人拿到位置與結構化事實，**要看內容由人在本機打開**。
所以人用面必須有自己的取文字實作。**解法已在 ADR §9.5 選定**：新增
`src/ltm/ui/read.py`，讀 `_plans/<rev>.json`，是人用面**唯一**的取文字實作，
**不進 `IMPLEMENTATIONS` 動詞表**。曝露面由提議中的 INV-23 管。

### G-3 · `plan` 只說有幾筆提議，不說是哪幾筆 〔高／要裁決〕〔**2026-09-11 已關**〕

**〔2026-09-16 補：同 G-1，這一節是 2026-09-10 的現況〕** 今天
`PlanResponse.ops` 是 `tuple[OpRef, ...]`，路徑是
`verbs.py` 的 `ops=tuple(_op_ref(row) for row in data["script"])`。以下原文保留。

`PlanResponse.ops` 是一個**數字**（`ops: float`）。真正的內容在 `_plans.script[]`：

    {"op": "delete", "cue_id": "c1644", "char_start": 0, "char_end": 1,
     "text": null, "code": "engine-loop", "origin": "detector:engine-loop"}

而 `accept --op N` 吃的是**索引**。也就是說今天**沒有任何一張臉**會告訴你
第 N 筆要刪的是什麼——CLI 的 `describe_plan()` 也沒有。「決」這個畫面的核心，
今天在兩張臉上都是空的。

**裁決題**：`PlanResponse` 要不要帶 `script`（結構化的、不含替換文字的刪除清單）？
`op/cue_id/char_start/char_end/code/origin` 全都是位置與代號，**沒有一個是稿子
內容**，所以帶了不違反 INV-12。

### G-4 · 沒有任何回應帶得出時間軸 〔中／要裁決〕

`cues[].start/end` 只在 `_plans` 檔裡。`show` 給的是 stretch 級的區間。
「點一句聽那一秒」是本產品判斷幻覺最快的動作（ADR §3-2），它需要 cue 級時間。
`ui/read.py` 讀得到，所以**這一條可以不動 schema**——除非代理人也該拿得到時間軸。

### G-5 · 音訊本身沒有路徑 〔中／GUI 自己的設計〕

`open` 是「把路徑交給作業系統」，不是「給我位元組」。瀏覽器面要
`<audio>` 能拖曳，就要 206 Partial Content——**`http.server` 的 Range 支援是
零**（本機 3.12.7 實測：`http.server` 原始碼裡 `Range` 出現 0 次），約 30 行自己寫。
已記在 ADR §7-1。

### G-6 · `evidence` 說了輸入，沒說是哪一條規則讓它成為 finding 〔中／低成本〕

2026-09-10 實測，0904 那份錄音的唯一一則 finding：

    [engine-loop] engine-loop:1644-1649 in cue 0-1
      cues=6, own_temperature=0, s4a_bad=0, s4a_neighbours=22, signals=1

`signals=1`。但架構文件 §3.3 寫的是「≥2 → Finding，1 → Note」。
兩者都沒錯：`detect/score.py` 的 `Scored.stands_alone`（commit `b6e5c1c`）讓
**夠長的相同字串連續段自己就能成案**。問題是**回應沒有說出這件事**——讀的人
會以為門檻壞了。`evidence` 應該帶一個 `stands_alone: 1.0`（或等效代號）。

### G-7 · 產物檔名宣稱了一個沒人量到的語言 〔中／新缺陷 I-18〕

同一版的三份紀錄互相矛盾：

| 說法 | 值 |
|---|---|
| 檔名 | `....r000.**zh**.srt` / `....r000.**zh**.txt` |
| `_origin.json` | `language_asked: null`、`language_detected: "en"` |
| stretch 表 | `language: "undetermined"`、`agreement: 0.311` |

一份英文為主、系統自己判為 `undetermined` 的錄音，產出的檔案叫 `.zh.`。
GUI 只要把檔名顯示出來，就會把這個沒人量過的宣稱端到人面前。
**這與 GUI 無關，是產品缺陷**，登記為 I-18。

### G-8 · 同一段音訊換個檔名就變成另一個 run 〔中／新缺陷 I-19〕

run 代號是 `<檔名主幹>-<內容雜湊前 8 碼>`。2026-09-10 把錄音從 mfp 的樹複製出來
（去掉了檔名裡的 ` (1)`）之後：

    0831討論grating fabrication和計畫 (1)-45a0cad2   (up to r012)
    0831討論grating fabrication和計畫-45a0cad2       (no round yet)

**雜湊一模一樣，run 卻是兩個。** 內容雜湊本來就是為了認出「同一段音訊」，
現在它被檔名主幹稀釋掉了。`runs` 畫面第一件事就是列 run，所以這條直接打在
GUI 的第一個畫面上。登記為 I-19。**裁決題**：同雜湊不同檔名，該合併、該提示，
還是就是兩個 run？

### G-9 · 解碼跑完 7 分 41 秒，畫面不說有沒有問題 〔低／低成本〕

實測 `decode` 跑完印的是：cue／slot／stretch／window 的數量，加四個路徑。
**「有沒有找到東西」不在上面**——要再跑一次 `plan` 才知道。等了七分半的人，
最想知道的那一件事需要第二個指令。

---

## 4. 兩張臉之間的規矩（已經有測試盯著）

1. **動詞只有一份實作**：`cli.VERBS is verbs.IMPLEMENTATIONS`，
   由 `test_agentapi_schema.py::test_both_faces_call_one_function` 用 `is` 斷言。
2. **參數映射也只有一份**：`cli._args_for()`。2026-09-10 新增
   `tests/test_two_faces.py`——十個動詞逐一 `signature.bind`，
   帶正對照（改名、加必填參數都必須爆）與涵蓋斷言（動詞表少一個就紅）。
   補的是 `ops/lessons.md` L-043 那個形狀：`open`／`cite`／`cancel`
   **在此之前沒有任何測試從 CLI 這條路呼叫過**。
3. **人用面的取文字實作也只准有一份**：`ui/read.py`（G-2）。
   GUI 不得自己去拼 `全文/`、`時軸/` 的檔案。

---

## 5. 這份契約怎麼改

- 機器可讀那一半是 `gui-contract.json`，帶 `contractVersion`。
- 改了接縫就 **bump `contractVersion`**，並在本檔末尾記一行變更。
- **不得在別處複述這份接縫**（AssetVault 的 `GUI-CONTRACT.md` 同一條規矩：
  雙源就是漂移的開始）。
- G-1／G-3／G-4 動的是代理人 schema，**必須走 PIM 增修 → 驗證硬閘**，
  不得在 PSM 裡順手改掉。

## 變更紀錄

| contractVersion | 日期 | 變更 |
|---|---|---|
| 1 | 2026-09-10 | 初版。九條缺口，其中 G-7／G-8 另登記為 I-18／I-19 |
| 2 | 2026-09-11 | **九條缺口關掉六條，全部是使用者已裁決、PIM v1.0／v1.1 已寫明形狀與對照的那六項。** G-1（`SpanRef.cue_id`）、G-3（`PlanResponse.ops` 變清單）、G-6（`evidence` 帶 `signals`，`stands_alone` 的判準因此看得出來）、G-7＝I-18（檔名量到才寫）、G-8＝I-19（同雜湊同一個 run，並且出聲）、G-9（`decode` 說出本輪找到什麼）。新增 `src/ltm/ui/read.py` 作為「給人看的文字」唯一實作，`readSource`／`joinRule` 因此從約定變成有測試（`tests/test_ui_read.py`，含「採納之前行號成立、採納之後失效」的雙側對照）。**未關**：G-2（設計如此）、G-4（時間軸仍不在回應裡——但 `read.py` 從紀錄拿得到，人用面已經印得出時刻）、G-5（音訊 bytes，屬介面自己的建置）。**INV-23 尚未實作**，因為還沒有那一面 |

| 3 | 2026-09-11 | **迴路面蓋起來了，所以 `exposure` 從提案變成有測試的條文。** `src/ltm/ui/serve.py`（PSM §16，F-1..F-6）：綁 `127.0.0.1` 臨機埠、權杖每次啟動重生、`TEXT_ROUTES` 是原始碼裡的字面 frozenset，**INV-23 的兩個對照跟伺服器同一個 commit**（沒登記卻回傳稿子的假路由必須被抓；登記在案的不可以被抓，而且要斷言它真的吐了字，否則一個什麼都不畫的頁面也會「乾淨」）。`readSource`／`joinRule` 現在真的有一個讀者在用。**`implementationRules` 第三條改寫**：`ui/read.py` 從「唯一取文字實作」變成「唯一讀取一輪紀錄的實作」，多了 `stretches_of`——理由是量出來的，`verbs.show` 只回答**解碼輪**，而讀者最可能打開的那個 run 停在 `r012`（偵測輪），於是「整體可不可信」在正好要看判決的那一版上是空的。四層都在該輪自己的 plan 紀錄裡（A-11），也就是本檔 `readSource` 早就寫著的那一份。**未關**：G-4（時間軸仍不在代理人回應裡）、G-5（音訊 bytes，§16.0 第 5 點刻意排除）。**新登記一條 `notClosed`**：ADR §10 那個「十個動詞兩張臉逐一比對」的行動項**沒有做**，頁面只驅動四個動詞 |

| 4 | 2026-09-11 | 新增 `notAScreenSource`：**`language_detected` 不是給人看的欄位**。它是 faster-whisper 的 `TranscriptionInfo.language`，在解碼迴圈**之前**用單一個 30 秒視窗算出來的（`language_detection_segments=1`），逐視窗的判定不會回寫。研究線同日量到：某段錄音的中文字數從 88 變成 7,290，這個欄位仍然是 `en (0.821)`，小數三位都沒動（`multilingual-verdict-2026-09-11.md` §4.1）。畫面上該說的語言一律取 `recording.stretches[].language`／`.agreement`——雙語錄音顯示 `undetermined, agreement 0.311` 是**對的**。以測試釘住（`tests/test_ui_serve.py::test_the_face_never_shows_the_engines_first_window_language`，正負對照各一），**沒有動 `verbs.py` 或 schema**：欄位留在紀錄裡當來源證據，只是不上畫面 |
| 5 | 2026-09-16 | **E-3 出貨（PSM §17，M-1..M-6）：`recording.report` 多一個欄位 `multilingual`。** 解碼預設改為逐視窗判斷語言；紀錄裡記的是子行程**自己回報**的值，2026-09-16 之前寫的 plan 沒有這個鍵，讀回來是 `null`（「沒回報」），**不回填 `false`**。臉**沒有多讀任何東西**：語言本來就從 stretch 表拿，`notAScreenSource` 不變——只是旗標打開之後，`language_detected` 從「偶爾」變成「經常」只代表第一個視窗，那一條存在的理由更充分了。代理人回應 `DecodeResponse.health` 多一個 `multilingual` 鍵（0/1/−1，型別不變）。另一件接縫上的變化不在這張契約裡但讀者會碰到：`decode --language` 單獨下會被擋（`bad-argument`／`language-with-multilingual`），要搭 `--no-multilingual` |

| 6 | 2026-09-16 | **頁面可以發動解碼了（PSM §19，GUI-1..GUI-6），這張臉第一次有寫入路徑，所以接縫真的變了。** 四條新路由：列出可解的錄音、開始一次、輪詢、請求停止（逐條與各自的控制項見 §2.1）。這一版同時把**路由表本身變成這個檔案裡的機器可讀欄位**（`routes`）——在此之前它只活在 `serve.py` 裡，契約這邊寫的是散文。三條唯讀路由的時候撐得住；**一個 commit 一次長出四條 JSON 路由**就撐不住了，而那正是 INV-23 指名的那句「多開一條 JSON 路由比較好接」。配套：`serve.py` 多一個 `JSON_ROUTES` 字面集合與一個 **import 時就跑的斷言，說它與 `TEXT_ROUTES` 不相交**；`tests/test_gui_contract.py`（新檔）**雙向**比對這個檔案與伺服器，逐欄比（method／carriesText／body 三欄都從伺服器的三張清單推得出來，所以這是比對，不是第二份描述），正對照由契約自己的列**生**出來而不是手寫。`notClosed.twoFacesOneImplementation` 從「頁面驅動四個動詞」改成六個：多的是 `intake`（在**本行程**裡呼叫，雜湊一個檔案是瞬間的事，放進子行程會讓「路徑給錯」與「解碼失敗」共用一個離開碼）與 `decode`（在**子行程**裡，用命令列呼叫——那個子行程本身就是 CLI 那張臉）。**未關**：G-2、G-4、G-5（`<audio>` 要的是位元組與 Range，跟面板送路徑去解碼是兩件事，這一版沒有碰） |

**這一版沒有走硬閘，這件事寫在這裡而不是省略。** 使用者 2026-09-11 裁定
不為 niche 可能性擋住先行開發，改採兩線並行（實作線／研究線）。這六項的形狀
與各自的兩個對照 **在 PIM v1.1 裡是白紙黑字**，施工照抄；**第十輪硬閘仍然欠著**，
對象是 PIM v1.1 全文，而屆時要一併審的是「已施工的六項與文件是否仍然一致」。
把它記在契約的變更紀錄裡，是因為欠一輪閘這件事只有寫在讀契約的人看得到的地方
才不會變成沒有人記得的事。

**後續（2026-09-11 當天稍晚）：第十輪跑完了，而且這張臉沒有被它動到。**
`6f941a3` 收下 K／L 兩份報告，對象是 PIM v1.2，**三個 BLOCK**：§2.1 的收斂句在
新增第三個欄位之後仍說「兩條契約」、E-3(e) 引用 INV-17 的形狀卻沒有 `unverified`
標記且 §5.5 權威清單沒有對應項、以及多語系證據 JSON 有兩個 case 缺 `measured_at`
而引用它的文件卻聲稱「每個 case 都有」。**兩份報告都沒有提到 INV-23、`src/ltm/ui`、
`serve` 或 F-1..F-6 任何一張卡**（grep 零命中），所以上面那句「若第十輪改了 INV-23，
臉要跟著改」這一次沒有兌現——**風險本身沒有消失，只是這一輪沒有發生**。
還欠的是那三個 BLOCK 的回答（v1.2 尚未通過），那是研究線的工作，不是這張臉的。
契約裡對應的欄位是 `notClosed.hardGate`，**沒有 bump `contractVersion`**：
接縫沒有改，改的是狀態，為狀態灌版本號會讓版本號不再代表接縫變動。

**〔2026-09-16，第十三輪 R13a-S4〕那三個 BLOCK 在 PIM v1.3 就答完了，之後又跑了
第十一、十二、十三輪**（v1.4／v1.5／v1.6），三輪合計十四個 BLOCK **沒有一個碰到
這張臉**。上一段的「v1.2 尚未通過」因此過期了七天，而它在 `gui-contract.json` 的
`notClosed.hardGate` 裡有一份副本、也一起過期——**同一個檔案裡另一個欄位
（`exposure.invariant`）卻說「第十輪還沒讀過 INV-23」，兩句互相矛盾**，因為兩句都是
在各自被寫下的那一刻為真的快照，而沒有人負責回讀。

**修法是給快照一個主人，不是把快照刪掉**：`changeDiscipline` 現在寫明，**答覆一輪
驗證閘的人，要在改 PIM 的同一個動作裡回讀契約的每一個散文欄位並蓋上回讀日期**。
上面那條「狀態不 bump 版本」的裁決不變，而且現在兩者的分工寫在契約裡：
**版本號追接縫，戳記追散文。**（一條規則有兩個讀法本身就是缺陷，所以這裡明講
哪一個成立。）
