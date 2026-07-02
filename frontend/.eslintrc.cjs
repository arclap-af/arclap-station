module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parser: "@typescript-eslint/parser",
  parserOptions: { ecmaVersion: 2022, sourceType: "module", ecmaFeatures: { jsx: true } },
  plugins: ["@typescript-eslint", "react", "react-hooks"],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react/recommended",
    "plugin:react-hooks/recommended",
  ],
  settings: { react: { version: "18.3" } },
  rules: {
    "react/react-in-jsx-scope": "off",
    "react/prop-types": "off",
    // Literal quotes/apostrophes in JSX text render fine; escaping every
    // one to &quot;/&apos; is noise, not correctness.
    "react/no-unescaped-entities": "off",
    "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    "@typescript-eslint/no-explicit-any": "error",
    "@typescript-eslint/no-empty-function": "off",
  },
  ignorePatterns: ["dist", "node_modules", "*.cjs"],
};
