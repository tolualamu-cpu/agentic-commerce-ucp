/** Tailwind build config — local build step only (not shipped to the browser).
 *
 * Replaces the in-browser cdn.tailwindcss.com JIT compiler with a prebuilt,
 * purged stylesheet at web/static/css/app.css. Rebuild after editing any
 * template:
 *
 *   npx tailwindcss@3 -c tailwind.config.js \
 *     -i web/static/css/tailwind.input.css \
 *     -o web/static/css/app.css --minify
 *
 * The scanner reads raw template text, so class names that appear as string
 * literals inside inline <script> blocks (toast tones, cart badge, chat
 * bubbles, ring states) are picked up automatically. The safelist below is a
 * defensive backstop for the few that are assembled via JS concatenation.
 */
module.exports = {
  content: ["./web/templates/**/*.html"],
  safelist: [
    // Toast tones (_toast.html — built from a `tone` variable)
    "bg-red-50", "border-red-200", "text-red-800",
    "bg-amber-50", "border-amber-200", "text-amber-800",
    "bg-slate-50", "border-slate-200", "text-slate-800",
    // Cart badge (created in JS on first add)
    "absolute", "-top-1", "-right-2", "bg-red-600", "text-white",
    "text-[10px]", "leading-none", "rounded-full",
    "min-w-[1.1rem]", "h-[1.1rem]", "px-1", "flex", "items-center",
    "justify-center", "font-bold", "transition",
    // Add/remove-to-cart button state swap (_chat_sse.html)
    "bg-slate-900", "hover:bg-slate-700",
    "bg-emerald-50", "text-emerald-700", "border", "border-emerald-200",
    "hover:bg-red-50", "hover:text-red-700", "hover:border-red-200",
    // Product detail thumbnail ring states
    "ring-1", "ring-2", "ring-slate-200", "ring-slate-900",
    // Chat bubbles
    "bg-cyan-100",
  ],
  theme: { extend: {} },
  plugins: [],
};
