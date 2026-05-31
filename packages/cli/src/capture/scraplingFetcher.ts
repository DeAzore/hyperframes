import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

export interface ScraplingResult {
  html: string;
  title: string;
  text: string;
  stealthy: boolean;
}

/** Returns true when python3 + scrapling are importable in the current env. */
export async function isScraplingAvailable(): Promise<boolean> {
  return new Promise((resolve) => {
    const proc = spawn("python3", ["-c", "import scrapling"], { stdio: "ignore" });
    proc.on("close", (code) => resolve(code === 0));
    proc.on("error", () => resolve(false));
  });
}

/**
 * Fetch a URL with Scrapling and return the page HTML + metadata.
 *
 * @param url       Target URL
 * @param stealthy  Use StealthyFetcher (Playwright-based, bypasses Cloudflare).
 *                  Requires `pip install "scrapling[fetchers]" && scrapling install`.
 * @param timeoutMs Kill the bridge process after this many ms (default 60 s).
 */
export async function fetchWithScrapling(
  url: string,
  stealthy = false,
  timeoutMs = 60_000,
): Promise<ScraplingResult> {
  const bridgePath = join(__dirname, "scrapling-bridge.py");
  const args: string[] = [bridgePath, url];
  if (stealthy) args.push("--stealthy");

  return new Promise((resolve, reject) => {
    const proc = spawn("python3", args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";

    const timer = setTimeout(() => {
      proc.kill();
      reject(new Error(`Scrapling timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    proc.stdout.on("data", (chunk: Buffer) => (stdout += chunk.toString()));
    proc.stderr.on("data", (chunk: Buffer) => (stderr += chunk.toString()));

    proc.on("close", (code) => {
      clearTimeout(timer);
      try {
        const result = JSON.parse(stdout.trim()) as Record<string, unknown>;
        if (typeof result.error === "string") {
          reject(new Error(`Scrapling: ${result.error}`));
        } else {
          resolve(result as unknown as ScraplingResult);
        }
      } catch {
        reject(new Error(`Scrapling bridge parse error (exit ${code}): ${stderr || stdout}`));
      }
    });

    proc.on("error", (err: Error) => reject(new Error(`Failed to spawn python3: ${err.message}`)));
  });
}
