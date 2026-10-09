import { afterEach, expect, it, vi } from 'vite-plus/test';
import { disposeRaster, drawRaster, tintRaster } from '../../src/tiled-raster';
afterEach(() => vi.unstubAllGlobals());
function fixture(failAt = Infinity) {
  const canvases: HTMLCanvasElement[] = [], reads: number[][] = [], draws: unknown[][] = [], outputs: Uint8ClampedArray[] = [];
  vi.stubGlobal('document', { createElement: () => {
    const canvas = { width: 0, height: 0, getContext: () => canvases.length === failAt ? null : {
      drawImage: (...args: unknown[]) => draws.push(args),
      getImageData: (...args: number[]) => { reads.push(args); return { data: new Uint8ClampedArray([200, 100, 50, 73]) }; },
      putImageData: (data: { data: Uint8ClampedArray }) => outputs.push(data.data),
    } } as unknown as HTMLCanvasElement;
    canvases.push(canvas); return canvas;
  } });
  return { canvases, reads, draws, outputs };
}
it('handles 16k maps with bounded canvases and reads while preserving alpha and source colors', () => {
  const f = fixture(), source = { naturalWidth: 16384, naturalHeight: 8192 } as HTMLImageElement;
  const raster = tintRaster(source, '#80ff00');
  expect(raster.tiles).toHaveLength(32);
  expect(f.canvases.every(c => c.width <= 2048 && c.height <= 2048)).toBe(true);
  expect(f.reads.every(r => r[2] <= 2048 && r[3] <= 2048)).toBe(true);
  expect([...f.outputs[0]]).toEqual([100, 100, 0, 73]);
  expect(f.draws[31].slice(1)).toEqual([14336, 6144, 2048, 2048, 0, 0, 2048, 2048]);
  disposeRaster(raster); expect(f.canvases.every(c => c.width === 0 && c.height === 0)).toBe(true);
});
it('cleans partial allocations when a tile context cannot be created', () => {
  const f = fixture(2);
  expect(() => tintRaster({ naturalWidth: 4096, naturalHeight: 2048 } as HTMLImageElement, '#ffffff')).toThrow('allocate');
  expect(f.canvases.every(c => c.width === 0 && c.height === 0)).toBe(true);
});
it('draws only visible tiles and keeps partial edge tiles at their true size', () => {
  fixture(); const raster = tintRaster({ naturalWidth: 4100, naturalHeight: 2050 } as HTMLImageElement, '#ffffff');
  expect(raster.tiles.at(-1)?.canvas.width).toBe(4); expect(raster.tiles.at(-1)?.canvas.height).toBe(2);
  const drawImage = vi.fn(); drawRaster({ drawImage } as unknown as CanvasRenderingContext2D, raster, -2048, 0, 4100, 2050, 200, 200);
  expect(drawImage).toHaveBeenCalledTimes(1);
  expect(drawImage.mock.calls[0].slice(1)).toEqual([0, 0, 2048, 2048, 0, 0, 2048, 2048]);
});
it('rejects invalid image dimensions before allocating', () => {
  const f = fixture();
  expect(() => tintRaster({ naturalWidth: Infinity, naturalHeight: 2 } as HTMLImageElement, '#ffffff')).toThrow('dimensions');
  expect(f.canvases).toHaveLength(0);
});
