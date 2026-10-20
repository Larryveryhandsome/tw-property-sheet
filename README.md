# tw-property-sheet｜物調表自動產生器

房仲接到委託後，要填一張「物件調查表」：地址、地段、坪數、樓層、屋齡、結構、車位、周邊機能……
很多資料其實**謄本裡有、公開地圖查得到**，卻還是要一格一格手打。這個工具會：

- **輸入地址 → 自動查**：座標、縣市鄉鎮、村里、地段，以及最近的公園、車站、超市（公開服務，免金鑰）
- **上傳建物謄本 PDF → 自動填**：建號、坐落地號、主建物／附屬建物／公設坪數（平方公尺自動換坪）、層數、層次、完工日期、屋齡、結構
- 自動算**權狀合計坪數、單價（萬／坪）、屋齡**
- 一鍵產生 **A4 一頁的物調表 Excel**，**必填但沒填的欄位會標黃**
- （選用）接 Claude API 產生**案名**和**銷售重點**；不接也能用，會用簡單規則代替

> 範本是本專案自己設計的通用格式，不屬於任何品牌。自動查詢的資料來自公開服務，原則上僅供參考，物件資訊請以登記謄本、主管機關資料與現場確認為準。

---

## 適合誰

- **房仲經紀人、助理**：每次開發新物件都要填物調表的人
- **代書、地政士、不動產估價**：需要快速整理物件基本資料
- 想做房仲工具的開發者：可以參考謄本解析、國土測繪 API、Overpass 周邊查詢的寫法

---

## 怎麼用

**需要：** Python 3.10 以上（[python.org](https://www.python.org/downloads/)，Windows 安裝時勾選「Add python.exe to PATH」）。

1. 下載這個專案（綠色 **Code** 按鈕 → **Download ZIP**），解壓縮，在資料夾裡執行：

   ```bash
   pip install -r requirements.txt
   ```

2. （選用）把 `.env.example` 複製成 `.env`，改 `PSHEET_BRAND`（顯示在頁面和 Excel 上的店名）。
3. 啟動：
   - **Windows**：雙擊 **`背景啟動.vbs`**（背景執行，不跳黑色視窗；紀錄在 `data\psheet.log`）。停止用 **`停止.vbs`**。
   - **macOS／Linux**：`python3 main.py`
4. 打開 http://localhost:8000 ，照五個步驟填：
   1. **基本資料**：物件編號、地址 → 按「自動查詢」
   2. **物件資訊**：上傳建物謄本 PDF → 按「解析謄本」；選土地分區、外觀結構
   3. **坪數樓層**：檢查自動帶入的坪數、樓層、完工日
   4. **周邊資料**：學區、周邊機能、車位、管理費
   5. **確認下載**：產生案名、銷售重點 → **下載 Excel**

### 謄本與個資

- 謄本請用你**合法申請**的電子謄本。解析只在你自己的電腦上進行，不會上傳到別的地方。
- 解析**只抓物件資料，不抓所有權人、他項權利人等個人資料**。
- 有開 AI 功能時，送給 Claude 的只有物件條件（地址、坪數、格局、周邊），不含謄本原文。

### 自己改範本或選單

- **下拉選單**：改 `options.json`（例如把你們店的營業員、常用車站、市場加進去）。
- **版面**：改 `sheet/layout.py` 的 `LAYOUT`，範本和填寫位置會一起變，不會對不上。
- 想用自己畫好的 Excel：版面要和 `LAYOUT` 一致，在 `.env` 設 `PSHEET_TEMPLATE=你的檔案.xlsx`。

### 選用：AI 產生案名與銷售重點

在 `.env` 填 `ANTHROPIC_API_KEY`（到 [Claude Console](https://console.anthropic.com/) 申請），重新啟動即可。
AI 被要求只根據你填的資料寫，不寫保證增值等誇大字眼；產生後仍請自己看過再用。會花 API 費用。

---

## 資料來源

| 用途 | 服務 | 備註 |
|---|---|---|
| 地址轉座標 | [ArcGIS World Geocoding](https://developers.arcgis.com/rest/geocode/) | 不儲存結果的單筆查詢免金鑰 |
| 縣市、鄉鎮、村里、地段 | [內政部國土測繪中心開放 API](https://api.nlsc.gov.tw/) | 地號請看謄本或地籍圖 |
| 公園、車站、超市 | [OpenStreetMap](https://www.openstreetmap.org/copyright) Overpass API | © OpenStreetMap contributors；公開站台常限流，程式會依序換站 |

公開服務可能改版、限流或暫停，查不到的欄位會留空，照樣可以手動填。
Python 3.13 以後對憑證檢查變嚴格，部分政府網站的憑證會被誤擋；本程式**仍然驗證憑證**，只關掉那項嚴格檢查。

---

## 給開發者

| 檔案 | 用途 |
|---|---|
| `main.py` | FastAPI：頁面與 API |
| `sheet/layout.py` | 版面定義、欄位換算（合計坪數、單價、屋齡、組合字串） |
| `sheet/excel.py` | 用 openpyxl 畫範本、填資料、標黃 |
| `services/geo.py` | 定位、行政區、周邊設施 |
| `services/tenement.py` | 謄本 PDF 文字解析 |
| `services/ai.py` | 選用的 Claude 案名／銷售重點 |
| `static/` | 前端（五步驟表單） |

測試（不連網）：

```bash
python -m unittest discover -s tests
```

## 授權

[Apache License 2.0](LICENSE)
