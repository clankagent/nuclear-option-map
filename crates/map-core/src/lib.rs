//! Portable map math. World X points right and world +Z (north) points up.
//! Width/height are meters; screen coordinates and pan deltas are pixels.
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InvalidInput;
impl fmt::Display for InvalidInput {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("map inputs must be finite, dimensions and zoom factors positive, and results representable")
    }
}
impl std::error::Error for InvalidInput {}
type Result<T> = std::result::Result<T, InvalidInput>;
fn positive(v: f64) -> Result<f64> {
    if v.is_finite() && v > 0.0 {
        Ok(v)
    } else {
        Err(InvalidInput)
    }
}
fn pair(x: f64, y: f64) -> Result<[f64; 2]> {
    if x.is_finite() && y.is_finite() {
        Ok([x, y])
    } else {
        Err(InvalidInput)
    }
}

/// A viewport with zoom clamped to 0.25–64 times the aspect-preserving fit.
/// Resize retains the world center and relative zoom. Failed changes are atomic.
#[derive(Debug, Clone)]
pub struct MapViewport {
    world_width: f64,
    world_height: f64,
    width: f64,
    height: f64,
    fit_scale: f64,
    zoom: f64,
    center: [f64; 2],
}
impl MapViewport {
    pub fn new(world_width: f64, world_height: f64, width: f64, height: f64) -> Result<Self> {
        positive(world_width)?;
        positive(world_height)?;
        let mut viewport = Self {
            world_width,
            world_height,
            width,
            height,
            fit_scale: 1.0,
            zoom: 1.0,
            center: [0.0, 0.0],
        };
        viewport.resize(width, height)?;
        Ok(viewport)
    }
    pub fn resize(&mut self, width: f64, height: f64) -> Result<()> {
        positive(width)?;
        positive(height)?;
        let fit_scale = positive((width / self.world_width).min(height / self.world_height))?;
        positive(fit_scale * self.zoom)?;
        self.width = width;
        self.height = height;
        self.fit_scale = fit_scale;
        Ok(())
    }
    pub fn fit(&mut self) {
        self.zoom = 1.0;
        self.center = [0.0, 0.0];
    }
    pub fn scale(&self) -> f64 {
        self.fit_scale * self.zoom
    }
    pub fn zoom(&self) -> f64 {
        self.zoom
    }
    pub fn center_x(&self) -> f64 {
        self.center[0]
    }
    pub fn center_z(&self) -> f64 {
        self.center[1]
    }
    pub fn world_to_screen(&self, x: f64, z: f64) -> Result<[f64; 2]> {
        pair(x, z)?;
        pair(
            self.width / 2.0 + (x - self.center[0]) * self.scale(),
            self.height / 2.0 - (z - self.center[1]) * self.scale(),
        )
    }
    pub fn screen_to_world(&self, x: f64, y: f64) -> Result<[f64; 2]> {
        pair(x, y)?;
        pair(
            self.center[0] + (x - self.width / 2.0) / self.scale(),
            self.center[1] - (y - self.height / 2.0) / self.scale(),
        )
    }
    /// Positive deltas move map content right/down.
    pub fn pan(&mut self, dx: f64, dy: f64) -> Result<()> {
        pair(dx, dy)?;
        self.center = pair(
            self.center[0] - dx / self.scale(),
            self.center[1] + dy / self.scale(),
        )?;
        Ok(())
    }
    pub fn zoom_at(&mut self, factor: f64, x: f64, y: f64) -> Result<()> {
        positive(factor)?;
        let anchor = self.screen_to_world(x, y)?;
        let zoom = (self.zoom * factor).clamp(0.25, 64.0);
        let scale = positive(self.fit_scale * zoom)?;
        let center = pair(
            anchor[0] - (x - self.width / 2.0) / scale,
            anchor[1] + (y - self.height / 2.0) / scale,
        )?;
        self.zoom = zoom;
        self.center = center;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn close(a: [f64; 2], b: [f64; 2]) {
        for i in 0..2 {
            assert!((a[i] - b[i]).abs() < 1e-8, "{a:?} != {b:?}");
        }
    }
    #[test]
    fn aspect_and_north() {
        let v = MapViewport::new(2000.0, 1000.0, 800.0, 800.0).unwrap();
        assert_eq!(v.scale(), 0.4);
        close(v.world_to_screen(-1000.0, 500.0).unwrap(), [0.0, 200.0]);
        close(v.world_to_screen(1000.0, -500.0).unwrap(), [800.0, 600.0]);
    }
    #[test]
    fn inverse_after_navigation() {
        let mut v = MapViewport::new(24000.0, 32000.0, 900.0, 500.0).unwrap();
        v.pan(83.0, -24.0).unwrap();
        v.zoom_at(3.2, 222.0, 81.0).unwrap();
        for point in [[0.0, 0.0], [-12500.0, 17800.0], [12.25, -99.5]] {
            let p = v.world_to_screen(point[0], point[1]).unwrap();
            close(v.screen_to_world(p[0], p[1]).unwrap(), point);
        }
    }
    #[test]
    fn zoom_keeps_cursor_anchor_and_clamps() {
        let mut v = MapViewport::new(2000.0, 1000.0, 800.0, 600.0).unwrap();
        let before = v.screen_to_world(73.0, 93.0).unwrap();
        v.zoom_at(4.0, 73.0, 93.0).unwrap();
        close(v.screen_to_world(73.0, 93.0).unwrap(), before);
        v.zoom_at(f64::MAX, 73.0, 93.0).unwrap();
        assert_eq!(v.zoom(), 64.0);
        v.zoom_at(f64::MIN_POSITIVE, 73.0, 93.0).unwrap();
        assert_eq!(v.zoom(), 0.25);
        close(v.screen_to_world(73.0, 93.0).unwrap(), before);
    }
    #[test]
    fn pan_resize_fit() {
        let mut v = MapViewport::new(2000.0, 1000.0, 800.0, 600.0).unwrap();
        v.pan(40.0, 80.0).unwrap();
        close(v.world_to_screen(0.0, 0.0).unwrap(), [440.0, 380.0]);
        v.zoom_at(2.0, 400.0, 300.0).unwrap();
        v.resize(400.0, 300.0).unwrap();
        assert_eq!(v.zoom(), 2.0);
        close([v.center_x(), v.center_z()], [-100.0, 200.0]);
        v.fit();
        assert_eq!(v.zoom(), 1.0);
        assert_eq!(v.scale(), 0.2);
        close(v.world_to_screen(0.0, 0.0).unwrap(), [200.0, 150.0]);
    }
    #[test]
    fn unrepresentable_results_return_errors() {
        assert!(
            MapViewport::new(f64::MAX, f64::MAX, f64::MIN_POSITIVE, f64::MIN_POSITIVE).is_err()
        );
        let mut v = MapViewport::new(1000.0, 1000.0, 1.0, 1.0).unwrap();
        assert!(v.pan(f64::MAX, 0.0).is_err());
        assert!(v.screen_to_world(f64::MAX, 0.0).is_err());
        assert!(v.zoom_at(2.0, f64::MAX, 0.0).is_err());
        close([v.center_x(), v.center_z()], [0.0, 0.0]);
        assert_eq!(v.zoom(), 1.0);
    }
    #[test]
    fn invalid_changes_do_not_mutate() {
        for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(MapViewport::new(bad, 100.0, 500.0, 500.0).is_err());
        }
        let mut v = MapViewport::new(2000.0, 1000.0, 800.0, 600.0).unwrap();
        for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(v.resize(bad, 600.0).is_err());
            assert!(v.zoom_at(bad, 10.0, 20.0).is_err());
        }
        assert!(v.pan(f64::INFINITY, 1.0).is_err());
        assert!(v.world_to_screen(f64::NAN, 0.0).is_err());
        assert!(v.screen_to_world(0.0, f64::INFINITY).is_err());
        assert_eq!(v.scale(), 0.4);
        assert_eq!(v.zoom(), 1.0);
        close([v.center_x(), v.center_z()], [0.0, 0.0]);
    }
}
