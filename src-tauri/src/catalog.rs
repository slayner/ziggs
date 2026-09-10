use std::sync::{OnceLock, RwLock};

use serde::Serialize;

#[derive(Clone, Copy, Debug, Default, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CatalogState {
    #[default]
    Loading,
    Ready,
    Degraded,
}

#[derive(Clone, Copy, Debug, Default, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CatalogSource {
    #[default]
    Fallback,
    Cache,
    Backend,
}

#[derive(Clone, Debug, Default, Serialize, PartialEq, Eq)]
pub struct CatalogStatus {
    pub state: CatalogState,
    pub source: CatalogSource,
    pub count: usize,
    pub error: Option<String>,
}

static SPELL_STATUS: OnceLock<RwLock<CatalogStatus>> = OnceLock::new();
static ITEM_STATUS: OnceLock<RwLock<CatalogStatus>> = OnceLock::new();

fn catalog_status_after_degraded(mut status: CatalogStatus, error: impl ToString) -> CatalogStatus {
    status.state = CatalogState::Degraded;
    status.error = Some(error.to_string());
    status
}

fn spell_status() -> &'static RwLock<CatalogStatus> {
    SPELL_STATUS.get_or_init(|| RwLock::new(CatalogStatus::default()))
}

fn item_status() -> &'static RwLock<CatalogStatus> {
    ITEM_STATUS.get_or_init(|| RwLock::new(CatalogStatus::default()))
}

pub fn spells() -> CatalogStatus {
    spell_status()
        .read()
        .map(|status| status.clone())
        .unwrap_or_default()
}

pub fn items() -> CatalogStatus {
    item_status()
        .read()
        .map(|status| status.clone())
        .unwrap_or_default()
}

pub fn mark_spells_ready(source: CatalogSource, count: usize) {
    update(spell_status(), CatalogState::Ready, source, count, None);
}

pub fn mark_items_ready(source: CatalogSource, count: usize) {
    update(item_status(), CatalogState::Ready, source, count, None);
}

pub fn mark_spells_degraded(error: impl ToString) {
    mark_degraded(spell_status(), error);
}

pub fn mark_items_degraded(error: impl ToString) {
    mark_degraded(item_status(), error);
}

fn update(
    target: &RwLock<CatalogStatus>,
    state: CatalogState,
    source: CatalogSource,
    count: usize,
    error: Option<String>,
) {
    if let Ok(mut status) = target.write() {
        *status = CatalogStatus {
            state,
            source,
            count,
            error,
        };
    }
}

fn mark_degraded(target: &RwLock<CatalogStatus>, error: impl ToString) {
    if let Ok(mut status) = target.write() {
        *status = catalog_status_after_degraded(status.clone(), error);
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct CatalogStatuses {
    pub spells: CatalogStatus,
    pub items: CatalogStatus,
}

pub fn statuses() -> CatalogStatuses {
    CatalogStatuses {
        spells: spells(),
        items: items(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn degradation_preserves_the_last_known_catalog_metadata() {
        let previous = CatalogStatus {
            state: CatalogState::Ready,
            source: CatalogSource::Cache,
            count: 30,
            error: None,
        };

        assert_eq!(
            catalog_status_after_degraded(previous, "backend indisponível"),
            CatalogStatus {
                state: CatalogState::Degraded,
                source: CatalogSource::Cache,
                count: 30,
                error: Some("backend indisponível".into()),
            }
        );
    }
}
