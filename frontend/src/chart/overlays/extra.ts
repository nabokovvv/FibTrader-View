/**
 * Extra drawing tools ported from KLineChart Pro (https://github.com/klinecharts/pro, Apache-2.0)
 * and adapted to the klinecharts v10 overlay API: `precision` now comes from the chart symbol,
 * fill colours come from the theme's overlay styles instead of hard-coded blue.
 *
 * Licensed under the Apache License, Version 2.0 (licenses/Apache-2.0.txt), unlike the rest of the project (MIT);
 * see THIRD_PARTY_NOTICES.md.
 */
import { utils, type Bounding, type Coordinate, type LineAttrs, type OverlayTemplate, type TextAttrs, type PolygonAttrs, type CircleAttrs } from 'klinecharts'

function pricePrecision(chart: { getSymbol: () => { pricePrecision?: number } | null }): number {
  return chart.getSymbol()?.pricePrecision ?? 2
}

function getRotateCoordinate (coordinate: Coordinate, targetCoordinate: Coordinate, angle: number): Coordinate {
  const x = (coordinate.x - targetCoordinate.x) * Math.cos(angle) - (coordinate.y - targetCoordinate.y) * Math.sin(angle) + targetCoordinate.x
  const y = (coordinate.x - targetCoordinate.x) * Math.sin(angle) + (coordinate.y - targetCoordinate.y) * Math.cos(angle) + targetCoordinate.y
  return { x, y }
}

function getRayLine (coordinates: Coordinate[], bounding: Bounding): LineAttrs | LineAttrs[] {
  if (coordinates.length > 1) {
    let coordinate: Coordinate
    if (coordinates[0].x === coordinates[1].x && coordinates[0].y !== coordinates[1].y) {
      if (coordinates[0].y < coordinates[1].y) {
        coordinate = {
          x: coordinates[0].x,
          y: bounding.height
        }
      } else {
        coordinate = {
          x: coordinates[0].x,
          y: 0
        }
      }
    } else if (coordinates[0].x > coordinates[1].x) {
      coordinate = {
        x: 0,
        y: utils.getLinearYFromCoordinates(coordinates[0], coordinates[1], { x: 0, y: coordinates[0].y })
      }
    } else {
      coordinate = {
        x: bounding.width,
        y: utils.getLinearYFromCoordinates(coordinates[0], coordinates[1], { x: bounding.width, y: coordinates[0].y })
      }
    }
    return { coordinates: [coordinates[0], coordinate] }
  }
  return []
}

function getDistance (coordinate1: Coordinate, coordinate2: Coordinate,): number {
  const xDis = Math.abs(coordinate1.x - coordinate2.x)
  const yDis = Math.abs(coordinate1.y - coordinate2.y)
  return Math.sqrt(xDis * xDis + yDis * yDis)
}

const arrow: OverlayTemplate = {
  name: 'arrow',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length > 1) {
      const flag = coordinates[1].x > coordinates[0].x ? 0 : 1
      const kb = utils.getLinearSlopeIntercept(coordinates[0], coordinates[1])
      let offsetAngle
      if (kb) {
        offsetAngle = Math.atan(kb[0]) + Math.PI * flag
      } else {
        if (coordinates[1].y > coordinates[0].y) {
          offsetAngle = Math.PI / 2
        } else {
          offsetAngle = Math.PI / 2 * 3
        }
      }
      const rotateCoordinate1 = getRotateCoordinate({ x: coordinates[1].x - 8, y: coordinates[1].y + 4 }, coordinates[1], offsetAngle)
      const rotateCoordinate2 = getRotateCoordinate({ x: coordinates[1].x - 8, y: coordinates[1].y - 4 }, coordinates[1], offsetAngle)
      return [
        {
          type: 'line',
          attrs: { coordinates }
        },
        {
          type: 'line',
          ignoreEvent: true,
          attrs: { coordinates: [rotateCoordinate1, coordinates[1], rotateCoordinate2] }
        }
      ]
    }
    return []
  }
}

const circle: OverlayTemplate = {
  name: 'circle',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length > 1) {
      const radius = getDistance(coordinates[0], coordinates[1])
      return {
        type: 'circle',
        attrs: {
          ...coordinates[0],
          r: radius
        },
        styles: { style: 'stroke_fill' }
      }
    }
    return []
  }
}

const rect: OverlayTemplate = {
  name: 'rect',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length > 1) {
      return [
        {
          type: 'polygon',
          attrs: {
            coordinates: [
              coordinates[0],
              { x: coordinates[1].x, y: coordinates[0].y },
              coordinates[1],
              { x: coordinates[0].x, y: coordinates[1].y }
            ]
          },
          styles: { style: 'stroke_fill' }
        }
      ]
    }
    return []
  }
}

const triangle: OverlayTemplate = {
  name: 'triangle',
  totalStep: 4,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    return [
      {
        type: 'polygon',
        attrs: { coordinates },
        styles: { style: 'stroke_fill' }
      }
    ]
  }
}

const parallelogram: OverlayTemplate = {
  name: 'parallelogram',
  totalStep: 4,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length === 2) {
      return [
        {
          type: 'line',
          ignoreEvent: true,
          attrs: { coordinates }
        }
      ]
    }
    if (coordinates.length === 3) {
      const coordinate = { x: coordinates[0].x + (coordinates[2].x - coordinates[1].x), y: coordinates[2].y }
      return [
        {
          type: 'polygon',
          attrs: { coordinates: [coordinates[0], coordinates[1], coordinates[2], coordinate] },
          styles: { style: 'stroke_fill' }
        }
      ]
    }
    return []
  },
  performEventPressedMove: ({ points, performPointIndex, performPoint }) => {
    if (performPointIndex < 2) {
            points[0].value = performPoint.value
            points[1].value = performPoint.value
    }
  },
  performEventMoveForDrawing: ({ currentStep, points, performPoint }) => {
    if (currentStep === 2) {
            points[0].value = performPoint.value
    }
  }
}

const fibonacciCircle: OverlayTemplate = {
  name: 'fibonacciCircle',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length > 1) {
      const xDis = Math.abs(coordinates[0].x - coordinates[1].x)
      const yDis = Math.abs(coordinates[0].y - coordinates[1].y)
      const radius = Math.sqrt(xDis * xDis + yDis * yDis)
      const percents = [0.236, 0.382, 0.5, 0.618, 0.786, 1]
      const circles: CircleAttrs[] = []
      const texts: TextAttrs[] = []
      percents.forEach(percent => {
        const r = radius * percent
        circles.push(
          { ...coordinates[0], r }
        )
        texts.push({
          x: coordinates[0].x,
          y: coordinates[0].y + r + 6,
          text: `${(percent * 100).toFixed(1)}%`
        })
      })
      return [
        {
          type: 'circle',
          attrs: circles,
          styles: { style: 'stroke' }
        },
        {
          type: 'text',
          ignoreEvent: true,
          attrs: texts
        }
      ]
    }
    return []
  }
}

const fibonacciSegment: OverlayTemplate = {
  name: 'fibonacciSegment',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates, overlay, chart }) => {
    const lines: LineAttrs[] = []
    const texts: TextAttrs[] = []
    if (coordinates.length > 1) {
      const textX = coordinates[1].x > coordinates[0].x ? coordinates[0].x : coordinates[1].x
      const percents = [1, 0.786, 0.618, 0.5, 0.382, 0.236, 0]
      const yDif = coordinates[0].y - coordinates[1].y
      const points = overlay.points
            const valueDif = (points[0].value ?? 0) - (points[1].value ?? 0)
      percents.forEach(percent => {
        const y = coordinates[1].y + yDif * percent
                const price = ((points[1].value ?? 0) + valueDif * percent).toFixed(pricePrecision(chart))
        lines.push({ coordinates: [{ x: coordinates[0].x, y }, { x: coordinates[1].x, y }] })
        texts.push({
          x: textX,
          y,
          text: `${price} (${(percent * 100).toFixed(1)}%)`,
          baseline: 'bottom'
        })
      })
    }
    return [
      {
        type: 'line',
        attrs: lines
      }, {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}

const fibonacciSpiral: OverlayTemplate = {
  name: 'fibonacciSpiral',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates, bounding }) => {
    if (coordinates.length > 1) {
      const startRadius = getDistance(coordinates[0], coordinates[1]) / Math.sqrt(24)
      const flag = coordinates[1].x > coordinates[0].x ? 0 : 1
      const kb = utils.getLinearSlopeIntercept(coordinates[0], coordinates[1])
      let offsetAngle
      if (kb) {
        offsetAngle = Math.atan(kb[0]) + Math.PI * flag
      } else {
        if (coordinates[1].y > coordinates[0].y) {
          offsetAngle = Math.PI / 2
        } else {
          offsetAngle = Math.PI / 2 * 3
        }
      }
      const rotateCoordinate1 = getRotateCoordinate(
        { x: coordinates[0].x - startRadius, y: coordinates[0].y },
        coordinates[0],
        offsetAngle
      )
      const rotateCoordinate2 = getRotateCoordinate(
        { x: coordinates[0].x - startRadius, y: coordinates[0].y - startRadius },
        coordinates[0],
        offsetAngle
      )
      const arcs = [{
        ...rotateCoordinate1,
        r: startRadius,
        startAngle: offsetAngle,
        endAngle: offsetAngle + Math.PI / 2
      }, {
        ...rotateCoordinate2,
        r: startRadius * 2,
        startAngle: offsetAngle + Math.PI / 2,
        endAngle: offsetAngle + Math.PI
      }]
      let x = coordinates[0].x - startRadius
      let y = coordinates[0].y - startRadius
      for (let i = 2; i < 9; i++) {
        const r = arcs[i - 2].r + arcs[i - 1].r
        let startAngle = 0
        switch (i % 4) {
          case 0: {
            startAngle = offsetAngle
            x -= (arcs[i - 2].r)
            break
          }
          case 1: {
            startAngle = offsetAngle + Math.PI / 2
            y -= arcs[i - 2].r
            break
          }
          case 2: {
            startAngle = offsetAngle + Math.PI
            x += (arcs[i - 2].r)
            break
          }
          case 3: {
            startAngle = offsetAngle + Math.PI / 2 * 3
            y += arcs[i - 2].r
            break
          }
        }
        const endAngle = startAngle + Math.PI / 2
        const rotateCoordinate = getRotateCoordinate({ x, y }, coordinates[0], offsetAngle)
        arcs.push({
          ...rotateCoordinate,
          r,
          startAngle,
          endAngle
        })
      }
      return [
        {
          type: 'arc',
          attrs: arcs
        },
        {
          type: 'line',
          attrs: getRayLine(coordinates, bounding)
        }
      ]
    }
    return []
  }
}

const fibonacciSpeedResistanceFan: OverlayTemplate = {
  name: 'fibonacciSpeedResistanceFan',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates, bounding }) => {
    const lines1: LineAttrs[] = []
    let lines2: LineAttrs[] = []
    const texts: TextAttrs[] = []
    if (coordinates.length > 1) {
      const xOffset = coordinates[1].x > coordinates[0].x ? -38 : 4
      const yOffset = coordinates[1].y > coordinates[0].y ? -2 : 20
      const xDistance = coordinates[1].x - coordinates[0].x
      const yDistance = coordinates[1].y - coordinates[0].y
      const percents = [1, 0.75, 0.618, 0.5, 0.382, 0.25, 0]
      percents.forEach(percent => {
        const x = coordinates[1].x - xDistance * percent
        const y = coordinates[1].y - yDistance * percent
        lines1.push({ coordinates: [{ x, y: coordinates[0].y }, { x, y: coordinates[1].y }] })
        lines1.push({ coordinates: [{ x: coordinates[0].x, y }, { x: coordinates[1].x, y }] })
        lines2 = lines2.concat(getRayLine([coordinates[0], { x, y: coordinates[1].y }], bounding))
        lines2 = lines2.concat(getRayLine([coordinates[0], { x: coordinates[1].x, y }], bounding))
        texts.unshift({
          x: coordinates[0].x + xOffset,
          y: y + 10,
          text: `${percent.toFixed(3)}`
        })
        texts.unshift({
          x: x - 18,
          y: coordinates[0].y + yOffset,
          text: `${percent.toFixed(3)}`
        })
      })
    }
    return [
      {
        type: 'line',
        attrs: lines1
      },
      {
        type: 'line',
        attrs: lines2
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}

const fibonacciExtension: OverlayTemplate = {
  name: 'fibonacciExtension',
  totalStep: 4,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates, overlay, chart }) => {
    const fbLines: LineAttrs[] = []
    const texts: TextAttrs[] = []
    if (coordinates.length > 2) {
      const points = overlay.points
            const valueDif = (points[1].value ?? 0) - (points[0].value ?? 0)
      const yDif = coordinates[1].y - coordinates[0].y
      const percents = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
      const textX = coordinates[2].x > coordinates[1].x ? coordinates[1].x : coordinates[2].x
      percents.forEach(percent => {
        const y = coordinates[2].y + yDif * percent
                const price = ((points[2].value ?? 0) + valueDif * percent).toFixed(pricePrecision(chart))
        fbLines.push({ coordinates: [{ x: coordinates[1].x, y }, { x: coordinates[2].x, y }] })
        texts.push({
          x: textX,
          y,
          text: `${price} (${(percent * 100).toFixed(1)}%)`,
          baseline: 'bottom'
        })
      })
    }
    return [
      {
        type: 'line',
        attrs: { coordinates },
        styles: { style: 'dashed' }
      },
      {
        type: 'line',
        attrs: fbLines
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}

const gannBox: OverlayTemplate = {
  name: 'gannBox',
  totalStep: 3,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    if (coordinates.length > 1) {
      const quarterYDis = (coordinates[1].y - coordinates[0].y) / 4
      const xDis = coordinates[1].x - coordinates[0].x
      const dashedLines = [
        { coordinates: [coordinates[0], { x: coordinates[1].x, y: coordinates[1].y - quarterYDis }] },
        { coordinates: [coordinates[0], { x: coordinates[1].x, y: coordinates[1].y - quarterYDis * 2 }] },
        { coordinates: [{ x: coordinates[0].x, y: coordinates[1].y }, { x: coordinates[1].x, y: coordinates[0].y + quarterYDis }] },
        { coordinates: [{ x: coordinates[0].x, y: coordinates[1].y }, { x: coordinates[1].x, y: coordinates[0].y + quarterYDis * 2 }] },

        { coordinates: [{ ...coordinates[0] }, { x: coordinates[0].x + xDis * 0.236, y: coordinates[1].y }] },
        { coordinates: [{ ...coordinates[0] }, { x: coordinates[0].x + xDis * 0.5, y: coordinates[1].y }] },

        { coordinates: [{ x: coordinates[0].x, y: coordinates[1].y }, { x: coordinates[0].x + xDis * 0.236, y: coordinates[0].y }]},
        { coordinates: [{ x: coordinates[0].x, y: coordinates[1].y }, { x: coordinates[0].x + xDis * 0.5, y: coordinates[0].y }] }
      ]
      const solidLines = [
        { coordinates: [coordinates[0], coordinates[1]] },
        { coordinates: [{ x: coordinates[0].x, y: coordinates[1].y }, { x: coordinates[1].x, y: coordinates[0].y }] }
      ]
      return [
        {
          type: 'line',
          attrs: [
            { coordinates: [coordinates[0], { x: coordinates[1].x, y: coordinates[0].y }] },
            { coordinates: [{ x: coordinates[1].x, y: coordinates[0].y }, coordinates[1]] },
            { coordinates: [coordinates[1], { x: coordinates[0].x, y: coordinates[1].y }] },
            { coordinates: [{ x: coordinates[0].x, y: coordinates[1].y }, coordinates[0]] }
          ]
        },
        {
          type: 'polygon',
          ignoreEvent: true,
          attrs: {
            coordinates: [
              coordinates[0],
              { x: coordinates[1].x, y: coordinates[0].y },
              coordinates[1],
              { x: coordinates[0].x, y: coordinates[1].y }
            ]
          },
          styles: { style: 'fill' }
        },
        {
          type: 'line',
          attrs: dashedLines,
          styles: { style: 'dashed' }
        },
        {
          type: 'line',
          attrs: solidLines
        }
      ]
    }
    return []
  }
}

const threeWaves: OverlayTemplate = {
  name: 'threeWaves',
  totalStep: 5,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    const texts = coordinates.map((coordinate, i) => ({
      ...coordinate,
      text: `(${i})`,
      baseline: 'bottom'
    }))
    return [
      {
        type: 'line',
        attrs: { coordinates }
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}

const fiveWaves: OverlayTemplate = {
  name: 'fiveWaves',
  totalStep: 7,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    const texts = coordinates.map((coordinate, i) => ({
      ...coordinate,
      text: `(${i})`,
      baseline: 'bottom'
    }))
    return [
      {
        type: 'line',
        attrs: { coordinates }
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}

const eightWaves: OverlayTemplate = {
  name: 'eightWaves',
  totalStep: 10,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    const texts = coordinates.map((coordinate, i) => ({
      ...coordinate,
      text: `(${i})`,
      baseline: 'bottom'
    }))
    return [
      {
        type: 'line',
        attrs: { coordinates }
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}

const anyWaves: OverlayTemplate = {
  name: 'anyWaves',
  totalStep: Number.MAX_SAFE_INTEGER,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    const texts = coordinates.map((coordinate, i) => ({
      ...coordinate,
      text: `(${i})`,
      baseline: 'bottom'
    }))
    return [
      {
        type: 'line',
        attrs: { coordinates }
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}

const abcd: OverlayTemplate = {
  name: 'abcd',
  totalStep: 5,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    let acLineCoordinates: Coordinate[] = []
    let bdLineCoordinates: Coordinate[] = []
   
    const tags = ['A', 'B', 'C', 'D']
    const texts = coordinates.map((coordinate, i) => ({
      ...coordinate,
      baseline: 'bottom',
      text: `(${tags[i]})`
    }))
    if (coordinates.length > 2) {
      acLineCoordinates = [coordinates[0], coordinates[2]]
      if (coordinates.length > 3) {
        bdLineCoordinates = [coordinates[1], coordinates[3]]
      }
    }
    return [
      {
        type: 'line',
        attrs: { coordinates }
      },
      {
        type: 'line',
        attrs: [{ coordinates: acLineCoordinates }, { coordinates: bdLineCoordinates }],
        styles: { style: 'dashed' }
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}

const xabcd: OverlayTemplate = {
  name: 'xabcd',
  totalStep: 6,
  needDefaultPointFigure: true,
  needDefaultXAxisFigure: true,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates }) => {
    const dashedLines: LineAttrs[] = []
    const polygons: PolygonAttrs[] = []
    const tags = ['X', 'A', 'B', 'C', 'D']
    const texts = coordinates.map((coordinate, i) => ({
      ...coordinate,
      baseline: 'bottom',
      text: `(${tags[i]})`
    }))
    if (coordinates.length > 2) {
      dashedLines.push({ coordinates: [coordinates[0], coordinates[2]] })
      polygons.push({ coordinates: [coordinates[0], coordinates[1], coordinates[2]] })
      if (coordinates.length > 3) {
        dashedLines.push({ coordinates: [coordinates[1], coordinates[3]] })
        if (coordinates.length > 4) {
          dashedLines.push({ coordinates: [coordinates[2], coordinates[4]] })
          polygons.push({ coordinates: [coordinates[2], coordinates[3], coordinates[4]] })
        }
      }
    }
    return [
      {
        type: 'line',
        attrs: { coordinates }
      },
      {
        type: 'line',
        attrs: dashedLines,
        styles: { style: 'dashed' }
      },
      {
        type: 'polygon',
        ignoreEvent: true,
        attrs: polygons
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: texts
      }
    ]
  }
}


export const EXTRA_OVERLAYS: OverlayTemplate[] = [arrow, circle, rect, triangle, parallelogram, fibonacciCircle, fibonacciSegment, fibonacciSpiral, fibonacciSpeedResistanceFan, fibonacciExtension, gannBox, threeWaves, fiveWaves, eightWaves, anyWaves, abcd, xabcd]
