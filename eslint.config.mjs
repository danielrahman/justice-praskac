import globals from "globals";

export default [{
  languageOptions: {
    ecmaVersion: 2021,
    sourceType: "script",
    globals: {
      ...globals.browser,
      Chart: "readonly",
    }
  },
  rules: {
    "no-undef": "error",
    "no-dupe-keys": "error",
    "no-dupe-args": "error",
    "no-duplicate-case": "error",
    "no-unreachable": "error",
    "no-constant-condition": "error",
    "no-empty": "error",
    "valid-typeof": "error",
    "no-redeclare": "error",
  }
}];
