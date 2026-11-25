from html import escape


def temperature_chart_svg(points, entity_id, name, unit, start, end):
    width, height = 900, 500
    left, right, top, bottom = 78, 28, 62, 62
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [value for _, value in points]
    low, high = min(values), max(values)
    padding = max((high - low) * 0.1, 0.5)
    low -= padding
    high += padding
    duration = max((end - start).total_seconds(), 1)

    def x_position(point_time):
        return left + (point_time - start).total_seconds() / duration * plot_width

    def y_position(value):
        return top + (high - value) / (high - low) * plot_height

    grid = []
    for index in range(5):
        value = high - (high - low) * index / 4
        y = top + plot_height * index / 4
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" '
            'stroke="#d1d5db" stroke-width="1"/>'
            f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end" '
            f'font-size="12" fill="#374151">{value:.1f}</text>'
        )

    polyline = " ".join(
        f"{x_position(point_time):.1f},{y_position(value):.1f}"
        for point_time, value in points
    )
    start_label = start.astimezone().strftime("%Y-%m-%d %H:%M")
    end_label = end.astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<text x="{left}" y="28" font-size="18" font-weight="bold" '
        f'fill="#111827">{escape(str(name))} ({escape(entity_id)})</text>'
        f'{"".join(grid)}'
        f'<polyline points="{polyline}" fill="none" stroke="#2563eb" '
        'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<text x="{left}" y="{height-24}" font-size="12" fill="#374151">'
        f'{escape(start_label)}</text>'
        f'<text x="{width-right}" y="{height-24}" text-anchor="end" '
        f'font-size="12" fill="#374151">{escape(end_label)}</text>'
        f'<text x="20" y="{top+plot_height/2}" text-anchor="middle" '
        f'font-size="13" fill="#374151" transform="rotate(-90 20 {top+plot_height/2})">'
        f'Температура ({escape(str(unit))})</text>'
        "</svg>"
    )
