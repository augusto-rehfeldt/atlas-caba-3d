const requestedArea = new URLSearchParams(location.search).get("area");
window.SELECTED_AREA = (window.CABA_AREAS || []).find(area =>
  area.name.toLowerCase() === (requestedArea || "CABA completa").toLowerCase()
) || window.CABA_AREAS.find(area => area.name === "CABA completa");
window.SELECTED_RENDER = window.SELECTED_AREA.name === "CABA completa"
  ? window.CABA_RENDER
  : window.AREA_RENDERS?.[window.SELECTED_AREA.name];
if (window.SELECTED_RENDER) {
  window.CABA_RENDER_IMAGE = new Image();
  window.CABA_RENDER_IMAGE.src = window.SELECTED_RENDER.src;
}
document.write(`<script src="${window.SELECTED_RENDER?.shell || window.SELECTED_AREA.file}"><\/script>`);
