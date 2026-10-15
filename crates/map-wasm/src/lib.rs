use nuclear_option_map_core::{InvalidInput, MapViewport as Core};
use wasm_bindgen::prelude::*;
fn error(e: InvalidInput) -> JsValue {
    JsValue::from_str(&e.to_string())
}

#[wasm_bindgen]
pub struct MapViewport {
    inner: Core,
}

#[wasm_bindgen]
impl MapViewport {
    #[wasm_bindgen(constructor)]
    pub fn new(
        world_width: f64,
        world_height: f64,
        screen_width: f64,
        screen_height: f64,
    ) -> Result<MapViewport, JsValue> {
        Ok(Self {
            inner: Core::new(world_width, world_height, screen_width, screen_height)
                .map_err(error)?,
        })
    }
    pub fn resize(&mut self, width: f64, height: f64) -> Result<(), JsValue> {
        self.inner.resize(width, height).map_err(error)
    }
    pub fn fit(&mut self) {
        self.inner.fit();
    }
    #[wasm_bindgen(js_name = worldToScreen)]
    pub fn world_to_screen(&self, x: f64, z: f64) -> Result<Vec<f64>, JsValue> {
        self.inner
            .world_to_screen(x, z)
            .map(|p| p.to_vec())
            .map_err(error)
    }
    #[wasm_bindgen(js_name = screenToWorld)]
    pub fn screen_to_world(&self, x: f64, y: f64) -> Result<Vec<f64>, JsValue> {
        self.inner
            .screen_to_world(x, y)
            .map(|p| p.to_vec())
            .map_err(error)
    }
    pub fn pan(&mut self, dx: f64, dy: f64) -> Result<(), JsValue> {
        self.inner.pan(dx, dy).map_err(error)
    }
    #[wasm_bindgen(js_name = zoomAt)]
    pub fn zoom_at(&mut self, factor: f64, x: f64, y: f64) -> Result<(), JsValue> {
        self.inner.zoom_at(factor, x, y).map_err(error)
    }
    #[wasm_bindgen(js_name = setMaxZoom)]
    pub fn set_max_zoom(&mut self, max_zoom: f64) -> Result<(), JsValue> {
        self.inner.set_max_zoom(max_zoom).map_err(error)
    }
    pub fn scale(&self) -> f64 {
        self.inner.scale()
    }
    pub fn zoom(&self) -> f64 {
        self.inner.zoom()
    }
    #[wasm_bindgen(js_name = centerX)]
    pub fn center_x(&self) -> f64 {
        self.inner.center_x()
    }
    #[wasm_bindgen(js_name = centerZ)]
    pub fn center_z(&self) -> f64 {
        self.inner.center_z()
    }
}
