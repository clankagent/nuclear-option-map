import { validTint } from './catalog';
export interface RasterTile { x: number; y: number; canvas: HTMLCanvasElement }
export interface TiledRaster { width: number; height: number; tiles: RasterTile[] }
export function disposeRaster(raster: TiledRaster | null) {
  if (raster) { for (const tile of raster.tiles) { tile.canvas.width = 0; tile.canvas.height = 0; } raster.tiles.length = 0; }
}
/** Bound each canvas and pixel read to 2048², independent of source image dimensions. */
export function tintRaster(source: HTMLImageElement | ImageBitmap, tint: string): TiledRaster {
  if (!validTint(tint)) throw new TypeError('Tint must be #RRGGBB');
  const width = 'naturalWidth' in source ? source.naturalWidth : source.width, height = 'naturalHeight' in source ? source.naturalHeight : source.height;
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0) throw new TypeError('Map image dimensions must be positive safe integers');
  const result: TiledRaster = { width, height, tiles: [] };
  const channels = [1, 3, 5].map(start => parseInt(tint.slice(start, start + 2), 16) / 255);
  try {
    for (let y = 0; y < height; y += 2048) for (let x = 0; x < width; x += 2048) {
      const canvas = document.createElement('canvas'); result.tiles.push({ x, y, canvas });
      canvas.width = Math.min(2048, width - x); canvas.height = Math.min(2048, height - y);
      const ctx = canvas.getContext('2d'); if (!ctx) throw new Error('Unable to allocate map tile canvas');
      ctx.drawImage(source, x, y, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height);
      for (let i = 0; i < data.data.length; i += 4) for (let c = 0; c < 3; c++) data.data[i + c] = Math.round(data.data[i + c] * channels[c]);
      ctx.putImageData(data, 0, 0);
    }
    return result;
  } catch (error) { disposeRaster(result); throw error; }
}
export function drawRaster(ctx: CanvasRenderingContext2D, raster: TiledRaster, x: number, y: number, width: number, height: number, screenWidth: number, screenHeight: number) {
  const sx = width / raster.width, sy = height / raster.height;
  for (const tile of raster.tiles) {
    const dx = x + tile.x * sx, dy = y + tile.y * sy, dw = tile.canvas.width * sx, dh = tile.canvas.height * sy;
    if (dx + dw <= 0 || dy + dh <= 0 || dx >= screenWidth || dy >= screenHeight) continue;
    ctx.drawImage(tile.canvas, 0, 0, tile.canvas.width, tile.canvas.height, dx, dy, dw, dh);
  }
}
