// Every folder here with an index.jsx is a feature; it is picked up automatically (see app/customization.md).
const modules = import.meta.glob('./*/index.jsx', { eager: true })

export const FEATURES = Object.values(modules).map((m) => m.default)

// The feature that opens for an Action node's "ui" key (or a feature name).
export const featureFor = (key) => FEATURES.find((f) => f.name === key || (f.actions || []).includes(key))
