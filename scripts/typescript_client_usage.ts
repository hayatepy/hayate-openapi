import { createHayateClient } from "./api-client.js";

const client = createHayateClient({
  baseUrl: "https://api.example.test/v1",
  headers: async () => ({ authorization: "Bearer example" }),
});

async function checkTypes(): Promise<void> {
  const book = await client.post_typed_books_book_id({
    path: { book_id: "018f47a6-42d2-7f5a-a724-35d7d230ad42" },
    query: { notify: true },
    json: { title: "Typed contracts" },
  });
  if (book.status === 201) {
    const value = await book.json();
    const title: string = value.title;
    const notify: boolean = value.notify;
    void [title, notify];
  }

  await client.post_typed_covers({
    form: {
      alt: "A typed cover",
      cover: new Blob(["cover"], { type: "text/plain" }),
    },
  });

  const response = await client.post_sessions({
    form: {
      scopes: ["documents:read", "documents:write"],
      username: "ada",
    },
    header: { "x-request-id": "request-123" },
    cookie: { theme: "dark" },
  });

  if (response.status === 201) {
    const session = await response.json();
    const username: string = session.username;
    const scopes: string[] = session.scopes;
    void [username, scopes];
  }
}

void checkTypes;
