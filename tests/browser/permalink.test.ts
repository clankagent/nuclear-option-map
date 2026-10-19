import { describe, expect, it } from 'vite-plus/test';
import { buildHash, parseHash } from '../../demo/ui/permalink';

describe('view permalinks', () => {
  it('round trips map, variant, rounded view and encoded shared pins', () => {
    const hash = buildHash({ map: 'ignus-archipelago-46395', variant: 'satellite', centerX: -1200.4, centerZ: 3400.8, zoom: 12.526, sharedPins: [{ name: 'Ramp, north ~ A', x: 123.4, z: -98.8 }] });
    expect(hash).toBe('#m=ignus-archipelago-46395&v=satellite&cx=-1200&cz=3401&k=12.53&p=Ramp%2C%20north%20%7E%20A~123~-99');
    expect(parseHash(hash)).toEqual({ map: 'ignus-archipelago-46395', variant: 'satellite', centerX: -1200, centerZ: 3401, zoom: 12.53, sharedPins: [{ name: 'Ramp, north ~ A', x: 123, z: -99 }] });
  });
  it('ignores invalid, oversized and hostile fields without throwing', () => {
    expect(parseHash('#m=../bad&v=ok&cx=Infinity&cz=1000001&k=0&p=%3Cscript%3E~1~2,hi~NaN~3')).toEqual({ variant: 'ok', sharedPins: [{ name: '<script>', x: 1, z: 2 }] });
    expect(parseHash('#p=%E0%A4%A~1~2')).toEqual({});
    expect(parseHash('#m=' + 'a'.repeat(9000))).toEqual({});
    expect(parseHash('#p=' + 'x'.repeat(41) + '~1~2')).toEqual({});
    expect(parseHash('#m=valid&k=1025&cx=1e999')).toEqual({ map: 'valid' });
    expect(buildHash({ map: 'bad/path', centerX: Infinity, zoom: .1, sharedPins: [{ name: '', x: 0, z: 0 }] })).toBe('#');
  });
  it('strips control characters and caps shared pin count', () => {
    const hash = buildHash({ sharedPins: Array.from({ length: 10 }, (_, index) => ({ name: `P${index}\n`, x: index, z: 0 })) });
    expect(parseHash(hash).sharedPins).toHaveLength(8);
    expect(parseHash(hash).sharedPins?.[0].name).toBe('P0');
  });
});
