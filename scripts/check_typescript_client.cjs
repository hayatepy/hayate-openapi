"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

async function main() {
  const modulePath = path.resolve(process.argv[2]);
  const baseUrl = process.argv[3];
  if (!modulePath || !baseUrl) {
    throw new Error("usage: check_typescript_client.cjs CLIENT_MODULE BASE_URL");
  }

  const { createHayateClient } = require(modulePath);
  const client = createHayateClient({ baseUrl });
  const bookId = "018f47a6-42d2-7f5a-a724-35d7d230ad42";
  const bookResponse = await client.post_typed_books_book_id({
    path: { book_id: bookId },
    query: { notify: true },
    json: { title: "Typed contracts" },
  });
  if (bookResponse.status !== 201) {
    throw new Error(
      `expected typed book HTTP 201, received ${bookResponse.status}: ${await bookResponse.text()}`,
    );
  }
  const book = await bookResponse.json();
  const expectedBook = { id: bookId, notify: true, title: "Typed contracts" };
  assert.deepStrictEqual(book, expectedBook, "unexpected path/query/JSON response");

  const coverResponse = await client.post_typed_covers({
    form: {
      alt: "A typed cover",
      cover: new Blob(["cover"], { type: "text/plain" }),
    },
  });
  if (coverResponse.status !== 201) {
    throw new Error(
      `expected multipart HTTP 201, received ${coverResponse.status}: ${await coverResponse.text()}`,
    );
  }
  const cover = await coverResponse.json();
  assert.deepStrictEqual(
    cover,
    { alt: "A typed cover", name: "blob", size: 5 },
    "unexpected multipart response",
  );

  const response = await client.post_sessions({
    form: {
      scopes: ["documents:read", "documents:write"],
      username: "ada",
    },
    header: { "x-request-id": "request-123" },
    cookie: { theme: "dark" },
  });
  if (response.status !== 201) {
    throw new Error(`expected HTTP 201, received ${response.status}: ${await response.text()}`);
  }
  const body = await response.json();
  const expected = {
    request_id: "request-123",
    scopes: ["documents:read", "documents:write"],
    theme: "dark",
    username: "ada",
  };
  assert.deepStrictEqual(body, expected, "unexpected form/header/cookie response");
  console.log("generated zero-runtime TypeScript client round trip: PASS");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
