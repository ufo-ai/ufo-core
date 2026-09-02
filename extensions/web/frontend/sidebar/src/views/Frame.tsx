/** The page drawn without the shell, which is one page on both shells: the first run and the
 *  signed-out portal are read on it. The lanes shell's copy holds it, and each shell's bundler
 *  resolves its `@/…` imports against its own tree, so the sidebar half of the seam is this
 *  re-export and nothing more. It goes when the sidebar shell does. */
export { Frame, Head } from "../../../src/views/Frame";
