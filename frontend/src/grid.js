// One AG Grid (Community) theme and one set of column defaults for every grid in the app.
import { themeQuartz } from "ag-grid-community";

export const gridTheme = themeQuartz.withParams({
  fontFamily: "inherit",
  fontSize: 13,
  spacing: 6,
  accentColor: "#1d4ed8",
  headerFontWeight: 600,
  wrapperBorderRadius: 10,
});

export const defaultColDef = {
  sortable: true,
  resizable: true,
  suppressMovable: true,
  tooltipShowMode: "whenTruncated", // tooltips only on cells whose text is cut off
};

export const gridDefaults = { theme: gridTheme, defaultColDef, tooltipShowDelay: 300, enableCellTextSelection: true };
