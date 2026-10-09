import { expect, it } from 'vite-plus/test';
import { mapRasterCacheControl } from '../../vite.config';

it('caches only explicitly versioned raster assets, keeping mutable catalogs and contours fresh', () => {
  expect(mapRasterCacheControl('/maps/tiles/satellite/ignus/3/5/2.webp?v=1873f1313d902a78')).toContain('immutable');
  expect(mapRasterCacheControl('/maps/tiles/satellite-static/heartland-cpu-enhanced/overview.webp?v=113824c72d4db67c')).toContain('max-age=31536000');
  expect(mapRasterCacheControl('/maps/terrain/heartland-46396/topography-gutter/4/3/2.png?v=113824c72d4db67c')).toContain('immutable');
  for (const path of ['/maps/variants.json', '/maps/manifest.json?v=113824c72d4db67c', '/maps/terrain/a/contours/10/0/0/0.json?v=113824c72d4db67c', '/maps/tiles/satellite/ignus/3/5/2.webp', '/maps/tiles/satellite/ignus/3/5/2.webp?v=latest']) expect(mapRasterCacheControl(path)).toBeUndefined();
});
