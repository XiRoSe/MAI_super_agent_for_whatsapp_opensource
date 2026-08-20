import { theme, type ThemeConfig } from "antd";

// Mai "signal in the dark" theme — AntD dark algorithm heavily customized so it
// reads as a designed product, not stock Ant Design.
export const maiTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#21d4a8",
    colorInfo: "#21d4a8",
    colorBgBase: "#0a0e12",
    colorBgContainer: "#121a21",
    colorBgElevated: "#16212a",
    colorBorder: "#1f2a33",
    colorBorderSecondary: "#19222a",
    colorText: "#e8f0ee",
    colorTextSecondary: "#a8b6bd",
    colorTextTertiary: "#8a9aa3",
    borderRadius: 12,
    fontFamily: '"Satoshi", system-ui, sans-serif',
    fontSize: 14,
    controlHeight: 40,
    wireframe: false,
  },
  components: {
    Layout: {
      bodyBg: "#0a0e12",
      siderBg: "#0c1217",
      headerBg: "#0c1217",
    },
    Menu: {
      itemBg: "transparent",
      itemSelectedBg: "rgba(33,212,168,0.12)",
      itemSelectedColor: "#5effb0",
      itemHoverColor: "#5effb0",
      itemBorderRadius: 10,
    },
    Card: { colorBgContainer: "#121a21" },
    Button: { fontWeight: 600, primaryShadow: "0 8px 24px -8px rgba(33,212,168,0.5)" },
    Input: { colorBgContainer: "#0e151b" },
    Statistic: { contentFontSize: 28 },
  },
};
