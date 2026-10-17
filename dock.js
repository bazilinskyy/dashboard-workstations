// Docks a widget's window just below the widget before it in the order (widgets.json "order"; the first stays where
// it is) and tells the server where it now is, for the widget after it. Call after each fit, and every few seconds on
// an always-on screen to follow the widget above when it moves or changes height.
async function dock(name, height) {
  try {
    const p = await (await fetch('above?name=' + name, {cache: 'no-store'})).json();
    const x = p.h ? p.x : screenX, y = p.h ? p.y + p.h : screenY;
    if (screenX !== x || screenY !== y) moveTo(x, y);
    await fetch('layout?name=' + name, {method: 'POST', body: JSON.stringify({x, y, h: Math.max(height, outerHeight)})});
  } catch (e) {}
}
