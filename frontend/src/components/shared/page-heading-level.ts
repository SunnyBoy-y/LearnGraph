import { createContext } from 'react'

/** Embedded settings panels have a section heading, while the page owns the single h1. */
export const PageHeadingLevelContext = createContext<1 | 2>(1)
