const controlUrl = process.env.AGUI_CONTROL_URL
  ?? (process.env.AGUI_ENDPOINT_URL ? new URL(process.env.AGUI_ENDPOINT_URL).origin : "http://localhost:2027");

async function forward(request: Request, path: string[]) {
  const target = new URL(`${controlUrl}/${path.join("/")}`);
  const source = new URL(request.url);
  target.search = source.search;
  const response = await fetch(target, {
    method: request.method,
    headers: request.method === "GET" ? undefined : { "Content-Type": "application/json" },
    body: request.method === "GET" ? undefined : await request.text(),
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}

export async function GET(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(request, (await params).path);
}

export async function POST(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(request, (await params).path);
}
