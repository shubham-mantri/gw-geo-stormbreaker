import type { AiIndex, JsonRpcRequest, JsonRpcResponse, RpcContext } from "./types";
import { TOOL_DEFINITIONS, TOOL_HANDLERS } from "./tools";

const SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26"];
const SERVER_VERSION = "1.0.0";

export async function handleRpc(
  msg: JsonRpcRequest,
  rpcCtx: RpcContext,
): Promise<JsonRpcResponse | null> {
  if (!msg || msg.jsonrpc !== "2.0" || typeof msg.method !== "string") {
    return {
      jsonrpc: "2.0",
      id: msg?.id ?? null,
      error: { code: -32600, message: "Invalid Request" },
    };
  }

  if (msg.method.startsWith("notifications/")) return null;

  const reply = (result: unknown): JsonRpcResponse => ({
    jsonrpc: "2.0",
    id: msg.id ?? null,
    result,
  });
  const fail = (code: number, message: string): JsonRpcResponse => ({
    jsonrpc: "2.0",
    id: msg.id ?? null,
    error: { code, message },
  });

  switch (msg.method) {
    case "initialize": {
      const requested = (msg.params as Record<string, unknown>)
        ?.protocolVersion as string | undefined;
      const protocolVersion = SUPPORTED_PROTOCOL_VERSIONS.includes(requested ?? "")
        ? requested
        : SUPPORTED_PROTOCOL_VERSIONS[0];
      const siteName = rpcCtx.index?.site?.name || rpcCtx.rootDomain;
      return reply({
        protocolVersion,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: `${siteName} site assistant`, version: SERVER_VERSION },
        instructions: `Tools for querying ${siteName} (${rpcCtx.rootDomain}): search pages, read page content as markdown, get business/contact info, list services and products, read customer reviews, and submit a sales inquiry.`,
      });
    }
    case "ping":
      return reply({});
    case "tools/list":
      return reply({ tools: TOOL_DEFINITIONS });
    case "tools/call": {
      const params = msg.params as Record<string, unknown> | undefined;
      const name = params?.name as string | undefined;
      const handler = name ? TOOL_HANDLERS[name] : undefined;
      if (!handler) return fail(-32602, `Unknown tool: ${name}`);
      try {
        const result = await handler(
          (params?.arguments as Record<string, unknown>) ?? {},
          rpcCtx,
        );
        return reply(result);
      } catch (err) {
        console.error(`[MCP] tool ${name} failed:`, err);
        return reply({
          content: [{ type: "text", text: "The tool failed unexpectedly. Please try again." }],
          isError: true,
        });
      }
    }
    default:
      return fail(-32601, `Method not found: ${msg.method}`);
  }
}

export function getDiscoveryDocument(hostname: string, index: AiIndex | null) {
  const siteName = index?.site?.name || hostname;
  const siteDomain = index?.site?.domain || hostname;
  return {
    servers: [
      {
        name: `${siteName} MCP`,
        description: `Open MCP server for ${siteDomain}: site search, page content, business info, products, services, reviews, and inquiries.`,
        url: `http://${hostname}/api/mcp`,
        transport: "streamable-http",
        authentication: { type: "none" },
      },
    ],
  };
}

export type { AiIndex };
