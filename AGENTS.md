# Local agent installation

- The installed working agent must match the latest commit on
  `Seiger/DaVinci-Resolve-Agent:main` (`upstream/main`).
- Fetch and verify the remote main SHA before delivery. Verify installed package
  files against that exact tree and record the SHA in installation provenance.
- Develop and test fork/PR changes in isolated workspaces. Never install an
  unmerged PR branch into the working agent.
- Preserve active Resolve projects, sessions, pending receipts and backups.
  Updating Python discovery code does not require restarting Resolve.
