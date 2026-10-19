import type { AppContext } from "./app-context";
import { $ } from "./dom";
import { formatDistance, niceScaleBar } from "./geo";
import { joinRuler, rulerGroups, type Ruler, type RulerSegment } from "./ruler-groups";

type Draft = RulerSegment & {
  pointerId: number;
  x: number;
  y: number;
  scale: number;
  centerX: number;
  centerZ: number;
  width: number;
  height: number;
};
const svgNS = "http://www.w3.org/2000/svg";
function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>) {
  const node = document.createElementNS(svgNS, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
  return node;
}

/** Ephemeral, world-anchored rulers. Nothing is written to settings or view links. */
export class TemporaryRulers {
  private active = false;
  private mapId: string | null = null;
  private rulers: Ruler[] = [];
  private draft: Draft | null = null;
  private frame = 0;
  private readonly button = $<HTMLButtonElement>("scale-bar");
  private readonly overlay = document.querySelector<SVGSVGElement>("#temporary-rulers")!;
  constructor(private app: AppContext) {}

  private setActive(active: boolean) {
    this.cancelDraft();
    this.active = active;
    this.rulers = [];
    this.button.setAttribute("aria-pressed", String(active));
    this.button.title = active
      ? "Clear measurements and exit (Esc)"
      : "Measure temporarily: click, then drag on the map";
    this.overlay.toggleAttribute("hidden", !active);
    $("ruler-hint").hidden = !active;
    this.app.canvas.classList.toggle("measuring", active);
    if (active) {
      this.app.fly.cancel();
      this.app.menu.close();
      this.app.canvas.focus({ preventScroll: true });
    }
    this.schedule();
  }

  syncMap() {
    const next = this.app.session.current?.id ?? null;
    if (next !== this.mapId) {
      this.setActive(false);
      this.mapId = next;
    }
    this.button.disabled = !next || this.app.session.loading;
  }

  viewChanged() {
    const s = this.app.session.viewer?.state,
      d = this.draft;
    if (
      s &&
      d &&
      (s.scale !== d.scale ||
        s.centerX !== d.centerX ||
        s.centerZ !== d.centerZ ||
        s.width !== d.width ||
        s.height !== d.height)
    )
      this.cancelDraft();
    this.schedule();
  }

  private cancelDraft() {
    const draft = this.draft;
    this.draft = null;
    if (draft && this.app.canvas.hasPointerCapture(draft.pointerId))
      this.app.canvas.releasePointerCapture(draft.pointerId);
    this.schedule();
  }

  private consume(event: Event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  bind() {
    this.button.onclick = () => {
      if (!this.button.disabled) this.setActive(!this.active);
    };
    const canvas = this.app.canvas;
    canvas.addEventListener(
      "pointerdown",
      (event) => {
        if (!this.active) return;
        this.consume(event);
        if (event.button !== 0 || this.app.session.loading) return;
        if (this.draft || !event.isPrimary) {
          this.cancelDraft();
          return;
        }
        const s = this.app.session.viewer?.state;
        if (!s) return;
        const point = this.app.pointer.world(event),
          local = this.app.pointer.local(event);
        this.draft = {
          start: point,
          end: point,
          pointerId: event.pointerId,
          ...local,
          scale: s.scale,
          centerX: s.centerX,
          centerZ: s.centerZ,
          width: s.width,
          height: s.height,
        };
        canvas.focus({ preventScroll: true });
        canvas.setPointerCapture(event.pointerId);
        this.schedule();
      },
      { capture: true },
    );
    canvas.addEventListener(
      "pointermove",
      (event) => {
        if (!this.active) return;
        this.consume(event);
        if (this.draft?.pointerId !== event.pointerId) return;
        this.draft.end = this.app.pointer.world(event);
        this.schedule();
      },
      { capture: true },
    );
    canvas.addEventListener(
      "pointerup",
      (event) => {
        if (!this.active) return;
        this.consume(event);
        const d = this.draft;
        if (!d || d.pointerId !== event.pointerId || event.button !== 0) return;
        const local = this.app.pointer.local(event);
        if (Math.hypot(local.x - d.x, local.y - d.y) >= 4) {
          this.rulers.push(
            joinRuler({ start: d.start, end: this.app.pointer.world(event) }, this.rulers, d.scale),
          );
        }
        this.cancelDraft();
      },
      { capture: true },
    );
    for (const name of ["pointercancel", "lostpointercapture"] as const) {
      canvas.addEventListener(
        name,
        (event) => {
          if (!this.active) return;
          this.consume(event);
          if (event.pointerId === this.draft?.pointerId) this.cancelDraft();
        },
        { capture: true },
      );
    }
    canvas.addEventListener(
      "dblclick",
      (event) => {
        if (this.active) this.consume(event);
      },
      { capture: true },
    );
    canvas.addEventListener(
      "contextmenu",
      (event) => {
        if (!this.active) return;
        this.consume(event);
        this.cancelDraft();
        this.rulers.pop();
        this.schedule();
      },
      { capture: true },
    );
    // The navigation controller's long-press action is disabled in measurement mode.
    canvas.addEventListener(
      "touchstart",
      (event) => {
        if (this.active) event.stopImmediatePropagation();
      },
      { capture: true, passive: true },
    );
    document.addEventListener(
      "keydown",
      (event) => {
        if (!this.active || event.key !== "Escape") return;
        this.consume(event);
        this.setActive(false);
        canvas.focus({ preventScroll: true });
      },
      { capture: true },
    );
    window.addEventListener("blur", () => this.cancelDraft());
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.cancelDraft();
    });
  }

  private schedule() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }

  private draw() {
    this.overlay.replaceChildren();
    const viewer = this.app.session.viewer;
    if (!this.active || !viewer) return;
    const s = viewer.state;
    this.overlay.setAttribute("viewBox", `0 0 ${s.width} ${s.height}`);
    // One stripe is one quarter of the footer scale, never a stretched arbitrary fraction.
    const quarter = niceScaleBar(1 / s.scale, this.app.settings.units).pixels / 4;
    const defs = svg("defs", {}),
      pattern = svg("pattern", {
        id: "ruler-stripes",
        patternUnits: "userSpaceOnUse",
        width: quarter * 2,
        height: 3,
      });
    pattern.append(
      svg("rect", { width: quarter * 2, height: 3, class: "ruler-dark" }),
      svg("rect", { width: quarter, height: 3, class: "ruler-light" }),
    );
    defs.append(pattern);
    this.overlay.append(defs);
    const draft =
      this.draft &&
      Math.hypot(this.draft.end.x - this.draft.start.x, this.draft.end.z - this.draft.start.z) *
        s.scale >=
        4
        ? joinRuler(this.draft, this.rulers, s.scale)
        : null;
    const rulers = [...this.rulers, ...(draft ? [draft] : [])];
    const totals = new Map<number, number>();
    for (const group of rulerGroups(rulers)) {
      if (group.indices.length < 2) continue;
      const anchor = [...group.indices].reverse().find((index) => {
        const { start, end } = rulers[index];
        return Math.hypot(end.x - start.x, end.z - start.z) * s.scale >= 4;
      });
      if (anchor !== undefined) totals.set(anchor, group.meters);
    }
    for (const [index, ruler] of rulers.entries()) {
      let a = viewer.worldToScreen(ruler.start.x, ruler.start.z),
        b = viewer.worldToScreen(ruler.end.x, ruler.end.z);
      const length = Math.hypot(b.x - a.x, b.y - a.y);
      if (length < 4) continue;
      // Left-to-right orientation keeps labels upright for westward and southward drags.
      if (b.x < a.x || (b.x === a.x && b.y < a.y)) [a, b] = [b, a];
      const group = svg("g", {
        class: "temporary-ruler",
        transform: `translate(${a.x} ${a.y}) rotate(${(Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI})`,
      });
      const distance = Math.hypot(ruler.end.x - ruler.start.x, ruler.end.z - ruler.start.z);
      group.append(
        svg("rect", {
          x: 0,
          y: -1.5,
          width: length,
          height: 3,
          fill: "url(#ruler-stripes)",
          class: "ruler-stick",
        }),
      );
      const label = svg("text", {
        x: length / 2,
        y: -10,
        "text-anchor": "middle",
        class: "ruler-label",
      });
      label.textContent = formatDistance(distance, this.app.settings.units);
      group.append(label);
      const total = totals.get(index);
      if (total !== undefined) {
        const label = svg("text", {
          x: length / 2,
          y: 17,
          "text-anchor": "middle",
          class: "ruler-label ruler-total",
        });
        label.textContent = `Total ${formatDistance(total, this.app.settings.units)}`;
        group.append(label);
      }
      this.overlay.append(group);
    }
  }
}
