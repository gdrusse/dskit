export default async ({ directory, $ }) => {
  return {
    event: async ({ event }) => {
      if (event.type !== "session.created") return
      try {
        const result = await $`git -C ${directory} pull --ff-only`
        const lines = result.stdout.toString().trim().split("\n")
        for (const line of lines.slice(-3)) console.log(line)
      } catch (error) {
        console.error("[session-pull]", error.stderr?.toString() ?? error.message ?? error)
      }
    },
  }
}
