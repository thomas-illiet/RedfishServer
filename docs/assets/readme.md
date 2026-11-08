# Project diagrams

[Documentation](../readme.md) · [Architecture](../architecture.md)

Each diagram has an **editable Draw.io source** and an **SVG export**.
SVGs are produced by Draw.io Desktop using the light theme, with the model
embedded. They do not depend on downloaded images or fonts. Sources use
native shapes, text and connectors rather than flattened images. Exports
also retain native SVG text: no HTML `foreignObject` blocks or bitmaps are
needed to display them.

| Diagram | Source | Export | Used in |
|---|---|---|---|
| Deployment of the three profiles | [deployment.drawio](deployment.drawio) | [deployment.svg](deployment.svg) | [Overview](../readme.md), [deployment](../deployment.md) |
| Loading data and processing requests | [architecture.drawio](architecture.drawio) | [architecture.svg](architecture.svg) | [Architecture](../architecture.md) |
| Resource navigation | [redfish-resources.drawio](redfish-resources.drawio) | [redfish-resources.svg](redfish-resources.svg) | [API](../api.md) |
| Simulation and power state | [monitoring-cycle.drawio](monitoring-cycle.drawio) | [monitoring-cycle.svg](monitoring-cycle.svg) | [Monitoring](../monitoring.md) |

## Edit and re-export

Open the `.drawio` file in Draw.io Desktop or diagrams.net, edit the objects
and save. Then export to SVG, keeping the same base name and the light theme.
Do not edit only the SVG: the source and illustration would diverge.

From the project root, with Draw.io Desktop installed on macOS:

```sh
"/Applications/draw.io.app/Contents/MacOS/draw.io" \
  --disable-update --export --format svg --embed-diagram --uncompressed \
  --theme light --border 16 \
  --output docs/assets/architecture.svg docs/assets/architecture.drawio
```

Repeat for the other three names. On another system, replace the executable
path with your local installation path. The initial export used Draw.io
Desktop **31.1.8**. No remote export service is required.

To export all four sources in a single invocation:

```sh
"/Applications/draw.io.app/Contents/MacOS/draw.io" \
  --disable-update --export --format svg --embed-diagram --uncompressed \
  --theme light --border 16 --output docs/assets/ \
  docs/assets/deployment.drawio docs/assets/architecture.drawio \
  docs/assets/redfish-resources.drawio docs/assets/monitoring-cycle.drawio
```

Keep text simple and disable automatic word wrapping in Draw.io; use explicit
line breaks. This preserves vector labels in renderers that do not support
embedded HTML.

## Include in the documentation

From a Markdown file located directly in `docs/`:

```md
![Redfish mock architecture](assets/architecture.svg)

[Editable Draw.io source](assets/architecture.drawio).
```

Alt text explains the diagram when the image is not displayed. After each
export, check label readability, arrows, margins and the absence of clipped
text. Keep both files together.
