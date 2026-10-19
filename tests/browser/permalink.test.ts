import { describe, expect, it } from 'vite-plus/test';
import { buildHash, parseHash } from '../../demo/ui/permalink';

describe('view permalinks', () => {
  it('round trips map, variant, and the rounded view', () => {
    const hash = buildHash({ map: 'ignus-archipelago-46395', variant: 'satellite', centerX: -1200.4, centerZ: 3400.8, zoom: 12.526 });
    expect(hash).toBe('#m=ignus-archipelago-46395&v=satellite&cx=-1200&cz=3401&k=12.53');
    expect(parseHash(hash)).toEqual({ map: 'ignus-archipelago-46395', variant: 'satellite', centerX: -1200, centerZ: 3401, zoom: 12.53 });
  });
  it('ignores invalid, oversized and hostile fields without throwing', () => {
    expect(parseHash('#m=../bad&v=ok&cx=Infinity&cz=1000001&k=0&p=%3Cscript%3E~1~2,hi~NaN~3')).toEqual({ variant: 'ok' });
    expect(parseHash('#p=%E0%A4%A~1~2')).toEqual({});
    expect(parseHash('#m=' + 'a'.repeat(9000))).toEqual({});
    expect(parseHash('#p=' + 'x'.repeat(41) + '~1~2')).toEqual({});
    expect(parseHash('#m=valid&k=1025&cx=1e999')).toEqual({ map: 'valid' });
    expect(buildHash({ map: 'bad/path', centerX: Infinity, zoom: .1 })).toBe('#');
  });
});
