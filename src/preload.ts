import { buildRenderUrl } from "./render";

// Skill icons are fetched as soon as a skill shows up in the meter, not when its row is
// opened, so the row opens with them ready. Each one is asked for once, and only a few at
// a time, since a big fight can bring in many skills at once and they all hit our backend.
const AT_ONCE = 4;

const requested = new Set<string>();
const waiting: string[][] = [];
// Holding on to them keeps them in the browser's memory.
const loaded: HTMLImageElement[] = [];
let running = 0;

function load(urls: string[], done: () => void): void {
  const [url, ...rest] = urls;
  if (!url) {
    done();
    return;
  }
  const image = new Image();
  image.onload = () => {
    loaded.push(image);
    done();
  };
  image.onerror = () => load(rest, done);
  image.src = url;
}

function startMore(): void {
  while (running < AT_ONCE && waiting.length > 0) {
    running += 1;
    load(waiting.shift() ?? [], () => {
      running -= 1;
      startMore();
    });
  }
}

export function preloadSkillIcon(apiBaseUrl: string, candidates: string[]): void {
  const key = candidates.join("\n");
  if (candidates.length === 0 || requested.has(key)) return;
  requested.add(key);
  waiting.push(candidates.map(candidate => buildRenderUrl(apiBaseUrl, "spell", candidate)));
  startMore();
}
