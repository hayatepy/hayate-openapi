# hayate-openapi 設計ドキュメント

> hayate アプリから OpenAPI 3.1 ドキュメントを生成する内部設計メモ(日本語)。
> 公開ドキュメントは英語先行。各節は「決定 / 理由 / 却下した代替案」の形を基本とする。

## TL;DR

- **コンセプトは一文で「アプリが既に知っていることだけから OpenAPI 3.1 を生成する」**。
  ルートは `app.routes`(本体 0.8 で公開)、リクエストスキーマは validator に渡した型、
  応答スキーマは加算的な `describe()` 注釈。魔法の推論はしない。
- **OpenAPI 3.1 = JSON Schema 2020-12** なので、スキーマ変換は
  `SchemaProvider` protocol(型 → JSON Schema dict)に外部化。
  msgspec / pydantic を guarded import で自動検出し、**コアの依存は hayate のみ**。
- 表面は 3 つ: `OpenApi(app, title=..., version=...)` を `register(app)` すると
  `GET /openapi.json` と対話型 `GET /docs` が生え、`generate()` は dict を返す
  (CLI / 静的出力用)。

```python
from hayate import Hayate, validator
from hayate_openapi import OpenApi, describe, validated
import msgspec

class BookIn(msgspec.Struct):
    title: str

app = Hayate()

@app.post("/books", validated("json", BookIn))   # validator + スキーマタグの糖衣
@describe(status=201, summary="Create a book")
async def create(c):
    book = c.req.valid("json")
    return c.json({"title": book.title}, status=201)

OpenApi(app, title="Bookstore", version="1.0.0").register(app)
```

## 1. なぜ作るか

- 3 点セット(auth + MCP + **OpenAPI**)の最後のピース(roadmap §1)。
  TS フロントとの型接続は「hayate-openapi で 3.1 を吐き、openapi-typescript で型を得る」
  レシピで担う(TS クライアント自前生成はしない — roadmap 非目標)。
- FastAPI が証明した「コードから API ドキュメントが常に最新で出る」体験を、
  ゼロ依存・standards-first の形で。pydantic 必須にはしない(msgspec が第一級)。

## 2. 規範とする標準(Normative References)

| 対象 | 文書 | 対応 |
|---|---|---|
| OpenAPI | OpenAPI Specification 3.1.x | 生成物の形。3.0 系は対象外(JSON Schema 方言が別物のため) |
| JSON Schema | 2020-12 draft | 3.1 のスキーマ方言。SchemaProvider の出力契約 |
| パス表記 | OpenAPI Path Templating | URLPattern `:id` → `{id}` の機械変換(§5) |
| エラー応答 | RFC 9457 Problem Details | 既定のエラーレスポンス文書(本体の `problem()` と整合) |

## 3. アーキテクチャ

```
アプリ:  validated("json", T) / @describe(...) / OpenApi(app).register(app)
─────────────────────────────────────────────
hayate-openapi:  Route 走査(app.routes)→ operation 合成 → components/$defs 集約
─────────────────────────────────────────────
SchemaProvider protocol:  型 → JSON Schema 2020-12(msgspec | pydantic | 自作)
─────────────────────────────────────────────
hayate 本体:  app.routes(0.8 で公開)/ validator / URLPattern
```

### 3.1 情報源は 3 つだけ(決定)

1. **`app.routes`** — method / pattern / middleware / handler の登録簿。
   本体 0.8 で公開させた(Hono も `app.routes` を公開している前例。
   private 属性を歩く案は凍結監査に載らないため却下)。
2. **validator タグ** — `validated(target, T)` は本体 `validator()` を包んで
   返す middleware 関数に `__openapi__ = (target, T)` 属性を付けるだけの糖衣。
   **本体の validator と 100% 互換**(タグの無い素の validator は「スキーマ情報なし」
   として黙って通す)。
3. **`describe()` タグ** — handler 属性 `__openapi__` に summary / tags /
   status / response 型 / operation_id を積む。すべて任意。

- **却下**: ハンドラシグネチャからの推論(FastAPI 型)— hayate のハンドラは
  `(c)` の 1 引数で型情報を持たない。validator が唯一の型接続点。
- **却下**: ルート登録の独自ラッパー(`openapi.get(...)`)— 二重の登録 API は
  house style「本体への変更要求ゼロ → ただし本体の責務は本体へ」の精神に反する。
  ルート列挙は本体の責務として昇格させた。

### 3.2 SchemaProvider(決定)

```python
class SchemaProvider(Protocol):
    def supports(self, type_: Any) -> bool: ...
    def schema(self, type_: Any) -> tuple[dict, dict]:
        """(その型の schema, 参照される $defs)を返す。"""
```

- 既定は自動検出チェーン: msgspec(`msgspec.json.schema_components`)→
  pydantic(`TypeAdapter(T).json_schema`)→ dict を JSON Schema Draft 2020-12
  として一度 compile し、format を含め request ごとに検証。
  ライブラリが無ければ該当 provider はスキップ(guarded import)。
- `$defs` は `components/schemas` に集約し、`$ref` を書き換える。
- **却下**: msgspec / pydantic への直接依存。生 schema の実行時契約には
  `jsonschema` を用い、型 provider は protocol 注入のまま保つ。

### 3.3 パス変換(決定)

- `:name` → `{name}`(path parameter、required、schema は string 既定)。
  `validated("param", T)` が同名 property を持つ場合はその schema で上書きし、
  route にない property は契約誤記として拒否する。path parameter は OpenAPI 規則により
  schema 側が optional でも required のまま。
- 正規表現制約 `:id([0-9]+)`(URLPattern の丸括弧構文)は `{id}` に落とし、
  制約は `schema.pattern` に転記。
- `*`(ワイルドカード)と WebSocket ルートは **ドキュメント対象外**(スキップ)。
  auth の `/api/auth/*` のような mount はそれ自身が API 文書を持つ(将来 §7)。
- query / header / cookie は `validated(target, T)` の object スキーマを property 単位の
  parameter 群に展開。header は Fetch が公開する lowercase 名を契約とし、OpenAPI が無視する
  `Accept` / `Content-Type` / `Authorization` は誤解を避けるため拒否する。
  form は requestBody(`application/x-www-form-urlencoded`)。

## 4. 出力の形(決定)

- `OpenApi.generate() -> dict` — 純関数。`openapi: "3.1.1"`, info, paths, components。
- 応答: `describe(response=T, status=201)` があれば該当 status に schema。
  無ければ `200: {description: "Successful response"}` のみ(捏造しない)。
- エラー: validator 付き operation には `400` の Problem Details 応答を自動記載
  (`application/problem+json`、RFC 9457 — 本体が実際に返すものを書くだけ)。
- `register(app)` は `GET /openapi.json` と対話型 `GET /docs` を追加(パス変更可、
  `docs_path=None` で UI 無効化)。両方とも生成文書自身からは除外する。
- UI は Scalar の standalone HTML API。Python/Node 依存は増やさず、browser が
  jsDelivr から読む版を immutable URL + SRI で固定する。inline script は使わず、
  title/config は HTML escape、既定 font fetch は無効、CSP は script origin と
  same-origin/HTTPS API 接続だけを許可する。`scalar_script_url="/assets/scalar.js"` で
  same-origin self-host に切替可能。telemetry と外部
  client/share/deploy/MCP-generation/developer-tools/AI agent 導線は既定で無効にし、
  仕様文書を第三者サービスへ送る操作を公式画面から除く。
- **変更した判断(0.3)**: 0.1 では「UI は一行HTMLで足りる」として利用者へ委ねたが、
  FastAPI の初回成功体験と比較すると、OpenAPI JSON があっても browser で即試せないことが
  採用摩擦として大きい。Scalar の standalone API なら package size/依存を増やさず、
  security boundary も小さく保てるため公式 surface に昇格した。

## 5. スコープ外(YAGNI リスト)

| やらないこと | 理由 |
|---|---|
| TS / Python クライアント生成 | openapi-typescript 等の成熟ツールに接続するのが役割(roadmap 非目標) |
| OpenAPI 3.0.x 出力 | JSON Schema 方言が別物。3.1 のみ |
| webhooks / callbacks / links | 証拠駆動で待つ |
| renderer asset の package 同梱 | Python wheel と依存グラフを肥大化させず、既定は SRI 固定 CDN、必要なら same-origin self-host |
| ランタイムのレスポンス検証 | 生成器はドキュメントを書くだけ。検証は validator の役割 |

## 6. リスクと対応

| リスク | 対応 |
|---|---|
| スキーマライブラリの JSON Schema 出力差異 | SchemaProvider の契約は「2020-12 の dict」のみ。差異はライブラリ側の責務とし、受け入れテストは公式 validator(openapi-spec-validator)通過で判定 |
| 本体 `app.routes` への依存 | 本体 0.8 の公開 API(凍結監査対象)。加算的で撤回リスク小 |
| describe 忘れで文書がスカスカ | 「書いていないものは出ない」は仕様(捏造しない)。lint 的な `--check-coverage` は将来判断 |

## 7. マイルストーン

| 版 | 内容 | 受け入れ基準 |
|---|---|---|
| ~~**v0.1**~~ | **完了(2026-07-23)**: generate() + validated() + describe() + mount + CLI | ✅ openapi-spec-validator 通過(テスト常設)。✅ openapi-typescript 7.13 が CLI 出力から型生成(CI常設)。✅ 3 provider テスト通過(13 テスト)。実装メモ: URLPattern の regex 構文は丸括弧 `:id([0-9]+)`(§5 修正済み)、WS ルートは HTTP メソッドのホワイトリストで除外 |
| v0.2 | **完了(2026-07-24)**: cookie/Bearer/OAuth2 security schemes、auth middleware からの operation security 推論、public override、multipart + binary file、`py.typed` | auth をマウントした文書の security と upload schema を openapi-spec-validator で検証。17 tests + strict mypy 6 files ✅ |
| v0.3 | **完了(2026-07-25)**: Scalar 対話型 docs(`/docs`)、SRI/CSP/XSS hardening、self-host/disable、内部 route の schema 除外 | 31 tests + strict mypy/ruff ✅。実 browser で描画 → path parameter 入力 → Test Request → hayate endpoint の 200 JSON を一周、console/CSP error 0 ✅ |
| v0.4 | **完了(2026-07-27)**: `param` / `header` / `cookie` validator の OpenAPI parameter 投影、route/schema 整合性検証、version drift gate | PyPI hayate 0.12.0 のみで 37 tests、OpenAPI 3.1 validator、openapi-typescript 7.13、strict mypy/ruff ✅ |
| v1.0 | API 凍結 | 本体 v1.0 より後 |

### 決定済み(2026-07-23)

| 項目 | 決定 |
|---|---|
| 名前 | **hayate-openapi**(配布名)/ `hayate_openapi`(import 名) |
| リポジトリ | `hayatepy/hayate-openapi`。private 開始、v0.1 完成時に公開判断 |
| ライセンス / 最低 Python | MIT / 3.12(本体に合わせる) |
| 依存 | `hayate>=0.8`(`app.routes`)のみ。msgspec / pydantic はオプショナル検出 |
