window.SmartInventoryPageModules = window.SmartInventoryPageModules || {};

window.registerSmartInventoryPage = function registerSmartInventoryPage(name, config) {
  window.SmartInventoryPageModules[name] = {
    name,
    route: `/${name}`,
    ...config
  };
};
